from __future__ import annotations

import json

from app import run_logger


def test_technical_status_is_fail_closed():
    assert run_logger._technical_status("done", []) == "completed"
    assert run_logger._technical_status("done", [{"error": "x"}]) == "completed_with_errors"
    assert run_logger._technical_status("done", [], {"valid": False}) == "technical_invalid"
    assert run_logger._technical_status("running", []) == "running"


def test_task_snapshots_keep_only_latest(tmp_path, monkeypatch):
    run_root = tmp_path / "run_logs"
    task_root = run_root / "tasks"
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", run_root)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", task_root)

    first = run_logger.save_task_snapshot(
        run_type="analysis",
        run_id=12,
        status="done",
        metrics={"requests": 1},
        details={"pairs_total": 1},
    )
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    assert first_payload["run_id"] == 12

    second = run_logger.save_task_snapshot(
        run_type="analysis",
        run_id=13,
        status="done",
        metrics={"requests": 2},
        details={"pairs_total": 2},
    )

    latest = task_root / "analysis" / "latest.json"
    assert first == latest
    assert second == latest
    assert latest.exists()
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["run_id"] == 13
    assert payload["technical_status"] == "completed"
    assert payload["execution_id"] == "analysis:13"
    assert payload["metrics"]["requests"] == 2
    assert payload["details"]["pairs_total"] == 2
    assert list((task_root / "analysis").glob("*.json")) == [latest]
