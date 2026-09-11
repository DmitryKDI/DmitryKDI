import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import LlmConfig
from app.matching import DocumentInput
import app.control_pair_vision as pair_vision


def _rf(page, key, name="Помещение"):
    return {"page": page, "key": key, "name": name}


def _doc(name, rooms, text):
    return DocumentInput(
        name=name,
        pages=1,
        text_facts=[{"page": 1, "text": text}],
        room_facts=[_rf(1, room) for room in rooms],
        discipline_code="ОВ",
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


def test_deterministic_and_semantic_vision_confirm_page_pair(monkeypatch):
    before = [_doc("pd.pdf", ["012"], "012 венткамера приточная установка")]
    after = [_doc("rd.pdf", ["012"], "012 венткамера приточная установка")]

    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed())
    seen = {}

    def fake_compare(*args, **kwargs):
        seen["clip"] = kwargs.get("clip_frac")
        return {
            "significant": [{
                "label": "Изменение",
                "change": "В помещении 012 изменена конфигурация оборудования",
                "severity": "major",
                "field_check": "проверить решение в помещении 012",
            }]
        }

    monkeypatch.setattr(pair_vision, "compare_page_pair", fake_compare)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"), max_pairs=2,
    )

    page_sources = {s.source for s in signals if s.domain == "page_pair"}
    assert page_sources == {"raster_diff", "vision_pair"}
    assert [s.key for s in signals if s.domain == "room"] == ["012"]
    assert diagnostics[0]["status"] == "significant"
    assert diagnostics[0]["rooms_mentioned"] == ["012"]
    assert seen["clip"] == (0.2, 0.2, 0.5, 0.5)


def test_structurally_same_pair_does_not_spend_vision_call(monkeypatch):
    before = [_doc("pd.pdf", ["140"], "140 вытяжная вентиляция")]
    after = [_doc("rd.pdf", ["140"], "140 вытяжная вентиляция")]
    calls = {"vision": 0}

    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: {
        "significant": False, "diff_ratio": 0.001, "changed_cells": 0,
        "local_cluster": False, "hot_zone": None,
    })

    def should_not_run(*args, **kwargs):
        calls["vision"] += 1
        raise AssertionError("semantic vision must be skipped")

    monkeypatch.setattr(pair_vision, "compare_page_pair", should_not_run)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )

    assert signals == []
    assert calls["vision"] == 0
    assert diagnostics[0]["status"] == "visually_same"


def test_room_number_must_be_grounded_on_both_pages(monkeypatch):
    before = [_doc("pd.pdf", ["147"], "147 вентиляция")]
    after = [_doc("rd.pdf", ["147"], "147 вентиляция")]
    monkeypatch.setattr(pair_vision, "visual_change_evidence", lambda *args, **kwargs: _changed(False))
    monkeypatch.setattr(
        pair_vision,
        "compare_page_pair",
        lambda *args, **kwargs: {
            "significant": [{"change": "Изменение около помещения 999", "label": "", "field_check": ""}]
        },
    )

    signals, _ = pair_vision.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )
    assert not any(signal.domain == "room" for signal in signals)
    assert {signal.source for signal in signals} == {"raster_diff", "vision_pair"}
