from app.entity_control import run_room_entity_controls
from app.llm import LlmConfig
from app.matching import DocumentInput
import app.entity_control as entity_control


def _doc(name, text, rooms, kind="text"):
    return DocumentInput(
        name=name,
        pages=1,
        text_facts=[{"page": 1, "text": text}],
        room_facts=[{"page": 1, "key": room, "name": "Помещение"} for room in rooms],
        discipline_code="ОВ",
        page_kinds={1: kind},
    )


def test_distribution_assignment_can_confirm_room_entity_mismatch(monkeypatch):
    before = [_doc("pd.pdf", "Таблица воздухообменов помещений", ["101"], "text")]
    after = [_doc("rd.pdf", "План вентиляции помещения 101", ["101"], "drawing")]

    monkeypatch.setattr(entity_control, "extract_table_page", lambda *args, **kwargs: {
        "rooms_seen": ["101"],
        "rooms": [{"room": "101", "system": "SYS-A", "entities": ["E-1"]}],
        "error": None,
    })
    monkeypatch.setattr(entity_control, "extract_plan_entities", lambda *args, **kwargs: [
        {"entity": "E-1", "nearest_room": "102", "system": "SYS-A"}
    ])

    signals, diagnostics = run_room_entity_controls(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )

    assert len(signals) == 1
    assert signals[0].source == "room_entity"
    assert signals[0].domain == "room"
    assert signals[0].key == "101"
    assert diagnostics[0]["status"] == "checked"
    assert diagnostics[0]["findings"][0]["finding_type"] == "entity_relocated"


def test_entity_control_does_not_inspect_random_rd_pages(monkeypatch):
    before = [_doc("pd.pdf", "Таблица воздухообменов помещений", ["101"], "text")]
    after = [_doc("rd.pdf", "План другого этажа", ["999"], "drawing")]
    calls = {"plan": 0}

    monkeypatch.setattr(entity_control, "extract_table_page", lambda *args, **kwargs: {
        "rooms_seen": ["101"],
        "rooms": [{"room": "101", "system": "SYS-A", "entities": ["E-1"]}],
        "error": None,
    })

    def unexpected_plan(*args, **kwargs):
        calls["plan"] += 1
        raise AssertionError("no grounded room on this RD page")

    monkeypatch.setattr(entity_control, "extract_plan_entities", unexpected_plan)
    signals, diagnostics = run_room_entity_controls(
        before, after, ["pd.pdf"], ["rd.pdf"],
        LlmConfig(provider="anthropic", api_key="fake"),
    )

    assert signals == []
    assert calls["plan"] == 0
    assert diagnostics[0]["status"] == "no_grounded_plan"
