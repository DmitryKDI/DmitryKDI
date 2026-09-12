import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.control_pair_candidates import ControlPair, candidate_pairs
import app.control_pair_candidates as candidates
import app.control_pair_runtime as runtime
from app.llm import LlmConfig
from app.matching import DocumentInput


def _doc(name, rooms, text, discipline="ОВ"):
    return DocumentInput(
        name=name,
        pages=1,
        text_facts=[{"page": 1, "text": text}],
        room_facts=[{"page": 1, "key": room, "name": "Помещение"} for room in rooms],
        discipline_code=discipline,
        page_kinds={1: "drawing"},
    )


def _base(monkeypatch, raster_significant=False):
    monkeypatch.setattr(runtime, "run_room_entity_controls", lambda *a, **k: ([], []))
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *a, **k: [
        ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))
    ])
    monkeypatch.setattr(runtime, "visual_change_evidence", lambda *a, **k: {
        "significant": raster_significant,
        "diff_ratio": .001 if not raster_significant else .05,
        "raw_diff_ratio": .02,
        "changed_cells": 0 if not raster_significant else 8,
        "local_cluster": False,
        "hot_zone": None,
        "alignment": {"scale": 1.0},
    })


def test_weak_raster_does_not_block_focused_vision(monkeypatch):
    _base(monkeypatch, raster_significant=False)
    calls = {"focused": 0}

    def focused(*args, **kwargs):
        calls["focused"] += 1
        return ([{"change": "изменена трасса", "rooms": ["101"]}], [], 1)

    monkeypatch.setattr(runtime, "compare_shared_rooms_focused", focused)
    before = [_doc("pd.pdf", ["101"], "101 вентиляция")]
    after = [_doc("rd.pdf", ["101"], "101 вентиляция")]
    signals, diagnostics = runtime.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=1,
    )
    assert calls["focused"] == 1
    assert any(s.source == "vision_pair" for s in signals)
    assert not any(s.source == "raster_diff" for s in signals)
    page = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert page["raster_significant"] is False


def test_strong_raster_is_preserved_as_hint(monkeypatch):
    _base(monkeypatch, raster_significant=True)
    monkeypatch.setattr(runtime, "compare_shared_rooms_focused", lambda *a, **k: ([], [], 1))
    monkeypatch.setattr(runtime, "semantic_scope_compare", lambda *a, **k: {"differences": []})
    before = [_doc("pd.pdf", ["101"], "101 вентиляция")]
    after = [_doc("rd.pdf", ["101"], "101 вентиляция")]
    signals, _ = runtime.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=1,
    )
    assert any(s.source == "raster_diff" for s in signals)


def test_whole_page_fallback_runs_even_with_weak_raster(monkeypatch):
    _base(monkeypatch, raster_significant=False)
    monkeypatch.setattr(runtime, "compare_shared_rooms_focused", lambda *a, **k: ([], [], 1))
    seen = {"whole": 0}

    def whole(*args, **kwargs):
        seen["whole"] += 1
        return {"differences": [{
            "change": "изменено подключение",
            "rooms": ["101"],
            "pd_observation": "А",
            "rd_observation": "Б",
        }]}

    monkeypatch.setattr(runtime, "semantic_scope_compare", whole)
    before = [_doc("pd.pdf", ["101"], "101 вентиляция")]
    after = [_doc("rd.pdf", ["101"], "101 вентиляция")]
    signals, _ = runtime.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=1,
    )
    assert seen["whole"] == 1
    assert any(s.domain == "room" and s.key == "101" for s in signals)


def test_candidate_expansion_allows_one_schematic_to_cover_multiple_plans(monkeypatch):
    before = [_doc("pd.pdf", ["101", "102", "201", "202"], "схема вентиляции")]
    after = [
        _doc("rd1.pdf", ["101", "102"], "план 1"),
        _doc("rd2.pdf", ["201", "202"], "план 2"),
    ]
    monkeypatch.setattr(candidates, "match_page_pairs", lambda *a, **k: [])
    pairs = candidate_pairs(before, after)
    covered = {(p.after_file_idx, p.matched_by) for p in pairs}
    assert (0, "room_overlap") in covered
    assert (1, "room_overlap") in covered


def test_ungrounded_room_number_never_becomes_room_signal(monkeypatch):
    _base(monkeypatch, raster_significant=False)
    monkeypatch.setattr(runtime, "compare_shared_rooms_focused", lambda *a, **k: ([], [], 1))
    monkeypatch.setattr(runtime, "semantic_scope_compare", lambda *a, **k: {
        "differences": [{"change": "изменение около 999", "rooms": ["999"]}]
    })
    before = [_doc("pd.pdf", ["101"], "101 вентиляция")]
    after = [_doc("rd.pdf", ["101"], "101 вентиляция")]
    signals, _ = runtime.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=1,
    )
    assert not any(s.domain == "room" for s in signals)
