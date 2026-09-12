import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import LlmConfig
from app.matching import DocumentInput
import app.control_pair_vision as pair_vision


def _rf(page, key, name="Помещение"):
    return {"page": page, "key": key, "name": name}


def _doc(name, rooms, text, discipline="ОВ"):
    return DocumentInput(
        name=name,
        pages=1,
        text_facts=[{"page": 1, "text": text}],
        room_facts=[_rf(1, room) for room in rooms],
        discipline_code=discipline,
        page_kinds={1: "drawing"},
    )


def _changed(local=True):
    return {
        "significant": True,
        "diff_ratio": 0.03,
        "changed_cells": 7,
        "local_cluster": local,
        "hot_zone": (0.2, 0.2, 0.5, 0.5) if local else None,
    }


def _disable_entity_control(monkeypatch):
    monkeypatch.setattr(pair_vision, "run_room_entity_controls", lambda *args, **kwargs: ([], []))


def test_deterministic_and_semantic_vision_confirm_page_pair(monkeypatch):
    before = [_doc("pd.pdf", ["012"], "012 венткамера приточная установка")]
    after = [_doc("rd.pdf", ["012"], "012 венткамера приточная установка")]
    _disable_entity_control(monkeypatch)
    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed())
    seen = {}

    def fake_compare(*args, **kwargs):
        seen["clip"] = kwargs.get("clip")
        return {
            "comparable": True,
            "differences": [{
                "label": "Изменение",
                "change": "В помещении 012 изменена конфигурация оборудования",
                "rooms": ["012"],
                "pd_observation": "одна конфигурация",
                "rd_observation": "другая конфигурация",
            }],
        }

    monkeypatch.setattr(pair_vision, "_semantic_scope_compare", fake_compare)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=2,
    )

    page_sources = {s.source for s in signals if s.domain == "page_pair"}
    assert page_sources == {"raster_diff", "vision_pair"}
    assert [s.key for s in signals if s.domain == "room"] == ["012"]
    page_diag = next(item for item in diagnostics if item.get("control_type") == "page_pair")
    assert page_diag["status"] == "significant"
    assert page_diag["rooms_mentioned"] == ["012"]
    assert seen["clip"] == (0.2, 0.2, 0.5, 0.5)


def test_raster_candidate_is_preserved_when_semantic_vision_finds_nothing(monkeypatch):
    before = [_doc("pd.pdf", ["140", "142"], "140 142 вытяжная вентиляция")]
    after = [_doc("rd.pdf", ["140", "142"], "140 142 вытяжная вентиляция")]
    _disable_entity_control(monkeypatch)
    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed(False))
    monkeypatch.setattr(
        pair_vision,
        "_semantic_scope_compare",
        lambda *args, **kwargs: {"comparable": True, "differences": []},
    )

    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )
    assert [(s.source, s.domain) for s in signals] == [("raster_diff", "page_pair")]
    page_diag = next(item for item in diagnostics if item.get("control_type") == "page_pair")
    assert page_diag["status"] == "raster_only"


def test_structurally_same_pair_does_not_spend_vision_call(monkeypatch):
    before = [_doc("pd.pdf", ["140"], "140 вытяжная вентиляция")]
    after = [_doc("rd.pdf", ["140"], "140 вытяжная вентиляция")]
    _disable_entity_control(monkeypatch)
    calls = {"vision": 0}

    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: {
        "significant": False, "diff_ratio": 0.001, "changed_cells": 0,
        "local_cluster": False, "hot_zone": None,
    })

    def should_not_run(*args, **kwargs):
        calls["vision"] += 1
        raise AssertionError("semantic vision must be skipped")

    monkeypatch.setattr(pair_vision, "_semantic_scope_compare", should_not_run)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )

    assert signals == []
    assert calls["vision"] == 0
    page_diag = next(item for item in diagnostics if item.get("control_type") == "page_pair")
    assert page_diag["status"] == "visually_same"


def test_room_number_must_be_grounded_on_both_pages(monkeypatch):
    before = [_doc("pd.pdf", ["147"], "147 вентиляция")]
    after = [_doc("rd.pdf", ["147"], "147 вентиляция")]
    _disable_entity_control(monkeypatch)
    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed(False))
    monkeypatch.setattr(
        pair_vision,
        "_semantic_scope_compare",
        lambda *args, **kwargs: {
            "comparable": True,
            "differences": [{
                "change": "Изменение около помещения 999",
                "rooms": ["999"],
                "label": "",
            }],
        },
    )

    signals, _ = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )
    assert not any(signal.domain == "room" for signal in signals)
    assert {signal.source for signal in signals} == {"raster_diff", "vision_pair"}


def test_ungrounded_whole_page_difference_starts_room_focused_fallback(monkeypatch):
    before = [_doc("pd.pdf", ["147"], "147 вентиляция")]
    after = [_doc("rd.pdf", ["147"], "147 вентиляция")]
    _disable_entity_control(monkeypatch)
    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed(False))
    monkeypatch.setattr(
        pair_vision,
        "_semantic_scope_compare",
        lambda *args, **kwargs: {
            "comparable": True,
            "differences": [{"change": "общая перестройка схемы", "rooms": []}],
        },
    )
    calls = {"focused": 0}

    def fake_focused(*args, **kwargs):
        calls["focused"] += 1
        return ([{
            "label": "Визуальное различие",
            "change": "в помещении изменена трасса",
            "rooms": ["147"],
            "pd_observation": "трасса А",
            "rd_observation": "трасса Б",
        }], [{"status": "focused_significant"}], 1)

    monkeypatch.setattr(pair_vision, "compare_shared_rooms_focused", fake_focused)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )
    assert calls["focused"] == 1
    assert any(signal.domain == "room" and signal.key == "147" for signal in signals)
    assert any(item.get("control_type") == "room_focus" for item in diagnostics)


def test_room_anchor_expansion_allows_one_pd_schematic_to_cover_multiple_rd_plans(monkeypatch):
    before = [_doc("pd.pdf", ["101", "102", "201", "202"], "схема вентиляции")]
    after = [
        _doc("rd-floor-1.pdf", ["101", "102"], "план вентиляции 1 этаж"),
        _doc("rd-floor-2.pdf", ["201", "202"], "план вентиляции 2 этаж"),
    ]
    monkeypatch.setattr(pair_vision, "match_page_pairs", lambda *args, **kwargs: [])

    pairs = pair_vision._candidate_pairs(before, after)
    covered = {(p.after_file_idx, p.after_page, p.matched_by) for p in pairs}
    assert (0, 1, "room_overlap") in covered
    assert (1, 1, "room_overlap") in covered
    assert all(len(p.shared_rooms) == 2 for p in pairs)
