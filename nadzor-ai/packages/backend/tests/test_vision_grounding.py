from types import SimpleNamespace

import app.vision_page_compare as vision_checks
from app.llm import LlmConfig


def test_composite_rooms_are_checked_separately(monkeypatch):
    finding = SimpleNamespace(
        finding_type="no_code_visual_check_needed",
        rooms=["пом. 101, 102"],
        sentence_pd="Для указанных помещений предусмотрено решение.",
    )
    room_index = {
        "101": [{"path": "rd.pdf", "page": 1, "name": "RD", "text": "101"}],
        "102": [{"path": "rd.pdf", "page": 2, "name": "RD", "text": "102"}],
    }
    calls = []

    def fake_check(path, page, sentence, rooms, config, discipline=None, timeout=120.0):
        calls.append((page, tuple(rooms)))
        return {"verdict": "absent" if rooms == ["102"] else "confirmed", "reason": "ok", "where": rooms[0]}

    monkeypatch.setattr(vision_checks, "check_requirement_on_page", fake_check)
    results = vision_checks.check_visual_candidates(
        [finding], room_index, LlmConfig(provider="anthropic", api_key="fake"),
        max_pages_per_finding=1,
    )
    assert calls == [(1, ("101",)), (2, ("102",))]
    assert [(x["rooms"], x["verdict"]) for x in results] == [(["101"], "confirmed"), (["102"], "absent")]


def test_no_grounded_page_means_no_vision_call(monkeypatch):
    finding = SimpleNamespace(
        finding_type="no_code_visual_check_needed",
        rooms=["неопознанная зона"],
        sentence_pd="Требование к зоне.",
    )
    calls = {"count": 0}

    def fail_if_called(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("unexpected vision call")

    monkeypatch.setattr(vision_checks, "check_requirement_on_page", fail_if_called)
    results = vision_checks.check_visual_candidates(
        [finding], {}, LlmConfig(provider="anthropic", api_key="fake")
    )
    assert calls["count"] == 0
    assert results[0]["verdict"] == "unclear"
    assert results[0]["pages_checked"] == 0
