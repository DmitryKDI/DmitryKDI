import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.focused_pair_vision as focused
from app.llm import LlmConfig


def test_select_rooms_uses_numeric_room_order(monkeypatch):
    monkeypatch.setattr(focused, "MAX_FOCUSED_ROOMS_PER_PAIR", 4)
    assert focused.select_rooms(["110", "002", "12", "003", "002"]) == ["002", "003", "12", "110"]


def test_room_sort_keeps_subrooms_after_base_room():
    rooms = ["012.2", "012", "011", "012.1"]
    assert sorted(rooms, key=focused.room_sort_key) == ["011", "012", "012.1", "012.2"]


def test_omitted_room_never_becomes_same():
    checked, missing = focused.normalize_checked_rooms(
        {
            "checked_rooms": [
                {
                    "room": "101",
                    "status": "same",
                    "pd_observation": "виден воздуховод",
                    "rd_observation": "виден воздуховод",
                    "change": "",
                }
            ]
        },
        ["101", "102"],
    )
    assert checked == [{
        "room": "101",
        "status": "same",
        "pd_observation": "виден воздуховод",
        "rd_observation": "виден воздуховод",
        "change": "",
    }]
    assert missing == ["102"]


def test_changed_without_description_is_downgraded_to_unclear():
    checked, missing = focused.normalize_checked_rooms(
        {"checked_rooms": [{"room": "101", "status": "changed", "change": ""}]},
        ["101"],
    )
    assert missing == []
    assert checked[0]["status"] == "unclear"


def test_same_without_two_observations_is_not_accepted_as_negative_evidence():
    checked, _ = focused.normalize_checked_rooms(
        {"checked_rooms": [{"room": "101", "status": "same", "pd_observation": "видно"}]},
        ["101"],
    )
    assert checked[0]["status"] == "unclear"


def test_compare_focused_turns_only_changed_rooms_into_findings(monkeypatch):
    monkeypatch.setattr(focused, "FOCUSED_ROOMS_PER_CALL", 4)
    monkeypatch.setattr(
        focused,
        "grounded_rows",
        lambda *args, **kwargs: (
            [("101", b"pd1"), ("102", b"pd2")],
            [("101", b"rd1"), ("102", b"rd2")],
            ["101", "102"],
            [],
        ),
    )
    monkeypatch.setattr(
        focused,
        "call_focused",
        lambda *args, **kwargs: {
            "checked_rooms": [
                {
                    "room": "101",
                    "status": "changed",
                    "pd_observation": "в ПД показан элемент",
                    "rd_observation": "в РД элемент отсутствует",
                    "change": "элемент исчез в РД",
                },
                {
                    "room": "102",
                    "status": "same",
                    "pd_observation": "схема читается",
                    "rd_observation": "схема читается",
                    "change": "",
                },
            ],
            "summary": "одна разница",
        },
    )

    findings, diagnostics, calls = focused.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101", "102"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=2,
    )
    assert calls == 1
    assert findings == [{
        "label": "Визуальное различие",
        "change": "элемент исчез в РД",
        "rooms": ["101"],
        "pd_observation": "в ПД показан элемент",
        "rd_observation": "в РД элемент отсутствует",
    }]
    assert diagnostics[0]["status"] == "focused_significant"
    assert diagnostics[0]["checked_rooms"][1]["status"] == "same"


def test_empty_model_answer_is_unclear_not_no_change(monkeypatch):
    monkeypatch.setattr(
        focused,
        "grounded_rows",
        lambda *args, **kwargs: ([('101', b'p')], [('101', b'r')], ['101'], []),
    )
    monkeypatch.setattr(focused, "call_focused", lambda *args, **kwargs: {})
    findings, diagnostics, calls = focused.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=1,
    )
    assert findings == []
    assert calls == 1
    assert diagnostics[0]["status"] == "focused_unclear"
    assert diagnostics[0]["omitted_by_model"] == ["101"]
