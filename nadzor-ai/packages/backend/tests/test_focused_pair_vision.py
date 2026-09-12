import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import LlmConfig
import app.high_recall_calls as calls
import app.high_recall_normalize as normalize
import app.high_recall_orchestrator as orchestrator
import app.high_recall_roi as roi


FULL = ["inventory", "topology", "connections", "parameters"]


def test_select_rooms_spreads_dense_sheet(monkeypatch):
    monkeypatch.setattr(roi, "MAX_FOCUSED_ROOMS_PER_PAIR", 4)
    assert roi.select_rooms([str(i) for i in range(1, 9)]) == ["1", "3", "6", "8"]


def test_room_sort_keeps_subrooms_after_base():
    assert sorted(["012.2", "012", "011", "012.1"], key=roi.room_sort_key) == ["011", "012", "012.1", "012.2"]


def test_expand_clip_adds_boundary_context():
    assert roi.expand_clip((0.0, 0.2, 0.4, 1.0), 0.25) == (0.0, 0.0, 0.5, 1.0)


def test_pass1_candidate_forces_changed_candidate():
    row = normalize.normalize_high_recall({
        "room_visible_pd": True,
        "room_visible_rd": True,
        "coverage": FULL,
        "status": "unchanged_candidate",
        "candidate_differences": [{
            "category": "connection",
            "side": "both",
            "description": "изменена точка подключения",
        }],
    }, "101")
    assert row["status"] == "changed_candidate"
    assert row["candidate_differences"][0]["category"] == "connection"


def test_unchanged_candidate_requires_full_visibility_and_coverage():
    row = normalize.normalize_high_recall({
        "room_visible_pd": False,
        "room_visible_rd": True,
        "coverage": ["inventory"],
        "status": "unchanged_candidate",
        "candidate_differences": [],
    }, "101")
    assert row["status"] == "unclear"


def test_verifier_omission_is_unclear_not_rejected():
    candidates = [{"description": "x"}, {"description": "y"}]
    rows = normalize.normalize_verification({
        "verified": [{"idx": 0, "verdict": "confirmed", "reason": "видно"}]
    }, candidates)
    assert rows[0]["verdict"] == "confirmed"
    assert rows[1]["verdict"] == "unclear"


def test_pass1_sends_four_separate_images(monkeypatch):
    seen = {}

    def fake_llm(config, system, user, images=None, **kwargs):
        seen["images"] = images
        return {"status": "unclear"}

    monkeypatch.setattr(calls, "call_llm_json", fake_llm)
    view = {
        "room": "101",
        "pd_general": b"a",
        "pd_room": b"b",
        "rd_general": b"c",
        "rd_room": b"d",
        "pd_text": "",
        "rd_text": "",
    }
    calls.call_high_recall(view, LlmConfig(provider="anthropic", api_key="fake"), "ОВ")
    assert len(seen["images"]) == 4
    assert len(set(seen["images"])) == 4


def test_orchestrator_runs_verifier_and_keeps_unclear_candidate(monkeypatch):
    monkeypatch.setattr(orchestrator, "MAX_FOCUSED_CALLS_PER_PAIR", 2)
    monkeypatch.setattr(orchestrator, "prioritize_rooms", lambda *a, **k: (["101"], []))
    monkeypatch.setattr(orchestrator, "grounded_view", lambda *a, **k: {
        "room": "101", "pd_general": b"a", "pd_room": b"b",
        "rd_general": b"c", "rd_room": b"d", "pd_clip": (0, 0, .5, .5),
        "rd_clip": (0, 0, .5, .5), "local_diff_score": 0.01,
        "pd_text": "", "rd_text": "",
    })
    monkeypatch.setattr(orchestrator, "call_high_recall", lambda *a, **k: {
        "room_visible_pd": True,
        "room_visible_rd": True,
        "coverage": FULL,
        "status": "changed_candidate",
        "candidate_differences": [{
            "category": "inventory", "side": "rd", "description": "элемент отсутствует"
        }],
    })
    monkeypatch.setattr(orchestrator, "call_verifier", lambda *a, **k: {
        "verified": [{"idx": 0, "verdict": "unclear", "reason": "тонкая линия"}]
    })
    findings, diagnostics, used = orchestrator.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=2, discipline="ОВ",
    )
    assert used == 2
    assert len(findings) == 1
    assert findings[0]["verification"] == "unclear"
    row = next(x for x in diagnostics if x.get("pass1"))
    assert row["selected_clips"]["101"]["image_layout"].startswith("D:")


def test_rejected_candidate_does_not_become_finding(monkeypatch):
    monkeypatch.setattr(orchestrator, "MAX_FOCUSED_CALLS_PER_PAIR", 2)
    monkeypatch.setattr(orchestrator, "prioritize_rooms", lambda *a, **k: (["101"], []))
    monkeypatch.setattr(orchestrator, "grounded_view", lambda *a, **k: {
        "room": "101", "pd_general": b"a", "pd_room": b"b",
        "rd_general": b"c", "rd_room": b"d", "pd_clip": (0, 0, .5, .5),
        "rd_clip": (0, 0, .5, .5), "local_diff_score": 0.2,
        "pd_text": "", "rd_text": "",
    })
    monkeypatch.setattr(orchestrator, "call_high_recall", lambda *a, **k: {
        "room_visible_pd": True, "room_visible_rd": True, "coverage": FULL,
        "candidate_differences": [{"category": "scale_shift", "description": "сдвиг", "side": "both"}],
        "status": "changed_candidate",
    })
    monkeypatch.setattr(orchestrator, "call_verifier", lambda *a, **k: {
        "verified": [{"idx": 0, "verdict": "rejected", "reason": "только регистрация"}]
    })
    findings, _, used = orchestrator.compare_shared_rooms_focused(
        "pd.pdf", 1, "rd.pdf", 1, ["101"],
        LlmConfig(provider="anthropic", api_key="fake"), max_calls=2,
    )
    assert used == 2
    assert findings == []
