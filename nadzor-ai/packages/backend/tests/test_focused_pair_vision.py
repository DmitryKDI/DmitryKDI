import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.focused_pair_vision as focused
from app.llm import LlmConfig


FULL_COVERAGE = ["inventory", "topology", "connections", "parameters"]


def test_select_rooms_spreads_coverage_across_large_room_set(monkeypatch):
    monkeypatch.setattr(focused, "MAX_FOCUSED_ROOMS_PER_PAIR", 4)
    selected = focused.select_rooms([str(i) for i in range(1, 9)])
    assert selected == ["1", "3", "6", "8"]


def test_room_sort_keeps_subrooms_after_base_room():
    rooms = ["012.2", "012", "011", "012.1"]
    assert sorted(rooms, key=focused.room_sort_key) == ["011", "012", "012.1", "012.2"]


def test_expand_clip_adds_context_without_leaving_page():
    assert focused._expand_clip((0.0, 0.2, 0.4, 1.0), 0.25) == (0.0, 0.0, 0.5, 1.0)


def test_omitted_room_never_becomes_same():
    checked, missing = focused.normalize_checked_rooms(
        {
            "checked_rooms": [
                {
                    "room": "101",
                    "status": "same",
                    "readability_pd": "good",
                    "readability_rd": "good",
                    "coverage": FULL_COVERAGE,
                    "pd_observation": "виден воздуховод",
                    "rd_observation": "виден воздуховод",
                    "differences": [],
                }
            ]
        },
        ["101", "102"],
    )
    assert checked[0]["status"] == "same"
    assert missing == ["102"]


def test_changed_without_concrete_difference_is_downgraded_to_unclear():
    checked, missing = focused.normalize_checked_rooms(
        {
            "checked_rooms": [{
                "room": "101",
                "status": "changed",
                "readability_pd": "good",
                "readability_rd": "good",
                "coverage": FULL_COVERAGE,
                "differences": [],
            }]
        },
        ["101"],
    )
    assert missing == []
    assert checked[0]["status"] == "unclear"


def test_same_requires_good_readability_and_full_sequence():
    checked, _ = focused.normalize_checked_rooms(
        {
            "checked_rooms": [{
                "room": "101",
                "status": "same",
                "readability_pd": "partial",
                "readability_rd": "good",
                "coverage": ["inventory", "topology"],
                "pd_observation": "частично видно",
                "rd_observation": "видно",
                "differences": [],
            }]
        },
        ["101"],
    )
    assert checked[0]["status"] == "unclear"


def test_architectural_room_name_change_is_not_engineering_evidence():
    checked, missing = focused.normalize_checked_rooms(
        {
            "checked_rooms": [{
                "room": "140",
                "status": "changed",
                "readability_pd": "good",
                "readability_rd": "good",
                "coverage": FULL_COVERAGE,
                "pd_observation": "учебное помещение",
                "rd_observation": "медицинский пункт",
                "differences": [{
                    "kind": "annotation",
                    "summary_ru": "Добавлен медицинский пункт",
                }],
            }]
        },
        ["140"],
    )
    assert missing == []
    assert checked[0]["status"] == "unclear"
    assert checked[0]["change"] == ""


def test_engineering_change_survives_even_if_room_name_also_changed():
    checked, _ = focused.normalize_checked_rooms(
        {
            "checked_rooms": [{
                "room": "140",
                "status": "changed",
                "readability_pd": "good",
                "readability_rd": "good",
                "coverage": FULL_COVERAGE,
                "pd_observation": "в ПД показан вытяжной воздуховод",
                "rd_observation": "в РД воздуховод отсутствует; помещение подписано иначе",
                "differences": [{
                    "kind": "layout",
                    "summary_ru": "вытяжная вентиляция исчезла",
                    "bbox_pd": [0.1, 0.2, 0.4, 0.5],
                    "bbox_rd": [0.1, 0.2, 0.4, 0.5],
                }],
            }]
        },
        ["140"],
    )
    assert checked[0]["status"] == "changed"
    assert "вентиляция" in checked[0]["change"]
    assert checked[0]["differences"][0]["bbox_pd"] == [0.1, 0.2, 0.4, 0.5]


def test_prioritize_rooms_uses_local_diff_score(monkeypatch):
    scores = {"101": 0.05, "102": 0.30, "103": 0.10}
    monkeypatch.setattr(
        focused,
        "_render_room_pair_for_score",
        lambda *args: (scores[args[-1]], (0.1, 0.1, 0.4, 0.4), (0.1, 0.1, 0.4, 0.4)),
    )
    ordered, diagnostics = focused.prioritize_rooms(
        "pd.pdf", 1, "rd.pdf", 1, ["101", "102", "103"]
    )
    assert ordered == ["102", "103", "101"]
    assert diagnostics[0]["room"] == "102"
    assert diagnostics[0]["priority_rank"] == 1


def test_compare_focused_turns_only_changed_room_into_finding(monkeypatch):
    monkeypatch.setattr(focused, "FOCUSED_ROOMS_PER_CALL", 1)
    monkeypatch.setattr(focused, "MAX_FOCUSED_CALLS_PER_PAIR", 2)
    monkeypatch.setattr(
        focused,
        "prioritize_rooms",
        lambda *args, **kwargs: (["101", "102"], []),
    )

    def fake_grounded(_bp, _bpage, _ap, _apage, rooms):
        room = rooms[0]
        return (
            [(room, b"pd")],
            [(room, b"rd")],
            [room],
            [],
            {room: {"local_diff_score": 0.2}},
        )

    monkeypatch.setattr(focused, "grounded_rows", fake_grounded)

    def fake_call(before, after, config):
        room = before[0][0]
        if room == "101":
            return {
                "checked_rooms": [{
                    "room": room,
                    "status": "changed",
                    "readability_pd": "good",
                    "readability_rd": "good",
                    "coverage": FULL_COVERAGE,
                    "pd_observation": "видно оборудование",
                    "rd_observation": "оборудование отсутствует",
                    "differences": [{
                        "kind": "equipment",
                        "summary_ru": "оборудование исчезло в РД",
                    }],
                }],
                "summary": "одно отличие",
            }
        return {
            "checked_rooms": [{
                "room": room,
                "status": "same",
                "readability_pd": "good",
                "readability_rd": "good",
                "coverage": FULL_COVERAGE,
                "pd_observation": "воздуховод читается",
                "rd_observation": "воздуховод читается",
                "differences": [],
            }]
        }

    monkeypatch.setattr(focused, "call_focused", fake_call)
    findings, diagnostics, calls = focused.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101", "102"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=2,
    )
    assert calls == 2
    assert len(findings) == 1
    assert findings[0]["rooms"] == ["101"]
    assert "исчезло" in findings[0]["change"]
    assert any(item.get("status") == "focused_significant" for item in diagnostics)
    assert any(item.get("status") == "focused_complete_no_change" for item in diagnostics)


def test_empty_model_answer_is_unclear_not_no_change(monkeypatch):
    monkeypatch.setattr(focused, "FOCUSED_ROOMS_PER_CALL", 1)
    monkeypatch.setattr(focused, "MAX_FOCUSED_CALLS_PER_PAIR", 1)
    monkeypatch.setattr(
        focused,
        "prioritize_rooms",
        lambda *args, **kwargs: (["101"], []),
    )
    monkeypatch.setattr(
        focused,
        "grounded_rows",
        lambda *args, **kwargs: (
            [("101", b"p")], [("101", b"r")], ["101"], [], {"101": {}}
        ),
    )
    monkeypatch.setattr(focused, "call_focused", lambda *args, **kwargs: {})
    findings, diagnostics, calls = focused.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=1,
    )
    assert findings == []
    assert calls == 1
    result_diag = next(item for item in diagnostics if item.get("requested_rooms"))
    assert result_diag["status"] == "focused_unclear"
    assert result_diag["omitted_by_model"] == ["101"]


def test_dense_pair_cannot_consume_global_budget(monkeypatch):
    monkeypatch.setattr(focused, "FOCUSED_ROOMS_PER_CALL", 1)
    monkeypatch.setattr(focused, "MAX_FOCUSED_CALLS_PER_PAIR", 2)
    monkeypatch.setattr(
        focused,
        "prioritize_rooms",
        lambda *args, **kwargs: ([str(i) for i in range(1, 11)], []),
    )

    def fake_grounded(_bp, _bpage, _ap, _apage, rooms):
        room = rooms[0]
        return ([(room, b"pd")], [(room, b"rd")], [room], [], {room: {}})

    monkeypatch.setattr(focused, "grounded_rows", fake_grounded)
    monkeypatch.setattr(
        focused,
        "call_focused",
        lambda before, after, config: {
            "checked_rooms": [{
                "room": before[0][0],
                "status": "same",
                "readability_pd": "good",
                "readability_rd": "good",
                "coverage": FULL_COVERAGE,
                "pd_observation": "виден воздуховод",
                "rd_observation": "виден воздуховод",
                "differences": [],
            }]
        },
    )

    _, diagnostics, calls = focused.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1,
        [str(i) for i in range(1, 11)],
        LlmConfig(provider="anthropic", api_key="fake"),
        max_calls=10,
    )
    assert calls == 2
    assert any(item["status"] == "focused_pair_budget" for item in diagnostics)
