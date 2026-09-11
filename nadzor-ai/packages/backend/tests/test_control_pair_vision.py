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


def test_pixel_and_semantic_vision_confirm_page_pair(monkeypatch):
    before = [_doc("pd.pdf", ["012"], "012 венткамера приточная установка")]
    after = [_doc("rd.pdf", ["012"], "012 венткамера приточная установка")]

    monkeypatch.setattr(pair_vision, "visual_diff_ratio", lambda *args, **kwargs: 0.25)
    monkeypatch.setattr(
        pair_vision,
        "compare_page_pair",
        lambda *args, **kwargs: {
            "significant": [{
                "label": "Венткамера 012",
                "change": "В помещении 012 изменена конфигурация приточной установки",
                "severity": "major",
                "field_check": "проверить установку в 012",
            }]
        },
    )

    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before,
        after,
        ["pd.pdf"],
        ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
        max_pairs=2,
    )

    page_sources = {s.source for s in signals if s.domain == "page_pair"}
    assert page_sources == {"pixel_diff", "vision_pair"}
    room_signals = [s for s in signals if s.domain == "room" and s.key == "012"]
    assert len(room_signals) == 1
    assert room_signals[0].source == "vision"
    assert diagnostics[0]["status"] == "significant"
    assert diagnostics[0]["rooms_mentioned"] == ["012"]


def test_unchanged_raster_does_not_spend_vision_call(monkeypatch):
    before = [_doc("pd.pdf", ["140"], "140 вытяжная вентиляция")]
    after = [_doc("rd.pdf", ["140"], "140 вытяжная вентиляция")]
    calls = {"vision": 0}

    monkeypatch.setattr(pair_vision, "visual_diff_ratio", lambda *args, **kwargs: 0.01)

    def should_not_run(*args, **kwargs):
        calls["vision"] += 1
        raise AssertionError("semantic vision must be skipped")

    monkeypatch.setattr(pair_vision, "compare_page_pair", should_not_run)
    signals, diagnostics = pair_vision.run_targeted_pair_vision(
        before,
        after,
        ["pd.pdf"],
        ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )

    assert signals == []
    assert calls["vision"] == 0
    assert diagnostics[0]["status"] == "visually_same"


def test_room_number_must_exist_on_both_pages(monkeypatch):
    before = [_doc("pd.pdf", ["147"], "147 вентиляция")]
    after = [_doc("rd.pdf", ["147"], "147 вентиляция")]
    monkeypatch.setattr(pair_vision, "visual_diff_ratio", lambda *args, **kwargs: 0.3)
    monkeypatch.setattr(
        pair_vision,
        "compare_page_pair",
        lambda *args, **kwargs: {
            "significant": [{"change": "Изменение около помещения 999", "label": "", "field_check": ""}]
        },
    )

    signals, _ = pair_vision.run_targeted_pair_vision(
        before,
        after,
        ["pd.pdf"],
        ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )
    assert not any(signal.domain == "room" for signal in signals)
    assert {signal.source for signal in signals} == {"pixel_diff", "vision_pair"}
