import json
from datetime import datetime, timezone
from pathlib import Path

from app import run_logger


def test_save_task_snapshot_keeps_only_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")
    path = run_logger.save_task_snapshot(
        run_type="analysis", run_id=17, status="done", provider="gigachat",
        model="GigaChat-3-Ultra", documents_before=["pd.pdf"],
        documents_after=["rd.pdf"], metrics={"calls": 5},
        details={"pairs_total": 5, "findings_total": 2}, errors=[],
    )
    assert path == tmp_path / "tasks" / "analysis" / "latest.json"
    assert list(path.parent.glob("*.json")) == [path]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_type"] == "analysis"
    assert payload["run_id"] == 17
    assert payload["technical_status"] == "completed"
    assert payload["execution_state"] == "completed"
    assert payload["execution_id"] == "analysis:17"


def test_task_log_replaces_previous_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")
    first = run_logger.save_task_snapshot(run_type="analysis", run_id=1, status="done")
    second = run_logger.save_task_snapshot(run_type="analysis", run_id=2, status="error")
    assert first == second
    folder = tmp_path / "tasks" / "analysis"
    assert sorted(folder.glob("*.json")) == [folder / "latest.json"]
    payload = json.loads(second.read_text(encoding="utf-8"))
    assert payload["run_id"] == 2
    assert payload["technical_status"] == "failed"


def test_task_log_paths_do_not_collide_between_run_types(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")
    when = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
    analysis, analysis_latest = run_logger._task_log_paths("analysis", 1, when)
    triangulated, triangulated_latest = run_logger._task_log_paths("triangulated", 1, when)
    assert analysis != triangulated
    assert analysis.name == "latest.json"
    assert triangulated.name == "latest.json"
    assert analysis == analysis_latest
    assert triangulated == triangulated_latest


def test_stage_logs_keep_one_latest_file_per_run_type(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    first_pd = run_logger.save(1, "pd", "done", "gigachat", "", ["pd-v1.pdf"], [], {})
    compliance = run_logger.save(77, "compliance", "done", "gigachat", "", ["pd-v1.pdf"], ["rd.pdf"], {})
    second_pd = run_logger.save(2, "pd", "done", "gigachat", "", ["pd-v2.pdf"], [], {})
    assert first_pd == second_pd == tmp_path / "pd_latest.json"
    assert compliance == tmp_path / "compliance_latest.json"
    files = {path.name for path in tmp_path.glob("*.json")}
    assert files == {"pd_latest.json", "compliance_latest.json"}
    payload = json.loads(second_pd.read_text(encoding="utf-8"))
    assert payload["run_id"] == 2


def test_purge_old_logs_removes_history_but_keeps_latest(tmp_path):
    (tmp_path / "tasks" / "analysis").mkdir(parents=True)
    (tmp_path / "1_20260912T000000Z.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pd_latest.json").write_text("{}", encoding="utf-8")
    old_task = tmp_path / "tasks" / "analysis" / "1_20260912T000000Z.json"
    latest_task = tmp_path / "tasks" / "analysis" / "latest.json"
    old_task.write_text("{}", encoding="utf-8")
    latest_task.write_text("{}", encoding="utf-8")
    run_logger._purge_old_logs(tmp_path)
    assert not (tmp_path / "1_20260912T000000Z.json").exists()
    assert (tmp_path / "pd_latest.json").exists()
    assert not old_task.exists()
    assert latest_task.exists()


def test_compact_triangulated_result_keeps_quality_diagnostics_only():
    compact = run_logger._compact_triangulated_result({
        "valid": True, "skipped_files": [], "llm": {"used": True, "call_failures": []},
        "not_run": ["some_optional_step"],
        "rooms": {"total_pd": 10, "total_rd": 12, "matched": 9, "unmatched": 4, "findings": [{"detail": "x"}]},
        "equipment": {"total_pd": 3, "total_rd": 4, "matched": 2, "unmatched": 3, "findings": [{"detail": "x"}, {"detail": "y"}]},
        "requirements": {"coded": {"total": 2}},
        "triangulation": {"signals_count": 8, "confirmed": [{"key": "A"}], "candidates": [{"key": "B"}, {"key": "C"}]},
        "verdicts": [{"key": "A"}], "escalation_tickets": [{"key": "B"}],
    })
    assert compact["rooms"]["findings_total"] == 1
    assert compact["equipment"]["findings_total"] == 2
    assert compact["triangulation"]["confirmed_total"] == 1
    assert compact["triangulation"]["candidates_total"] == 2
    assert "findings" not in compact["rooms"]


def test_run_logs_dir_lives_in_project_data_not_inside_packages():
    project_root = Path(run_logger.__file__).resolve().parents[3]
    target = run_logger.default_run_logs_dir()
    assert target == project_root / "data" / "run_logs"
    assert "packages" not in target.relative_to(project_root).parts


def test_legacy_directory_is_named_and_differs_from_the_current_one():
    project_root = Path(run_logger.__file__).resolve().parents[3]
    assert run_logger.LEGACY_RUN_LOGS_DIR != run_logger.RUN_LOGS_DIR
    assert run_logger.LEGACY_RUN_LOGS_DIR == project_root / "packages" / "data" / "run_logs"


def test_failed_calls_in_metrics_become_named_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    path = run_logger.save(15, "pd", "done", "gigachat", "", ["том.pdf"], [], {"requests": 10, "errors": 1, "invalid_results": 2})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["technical_status"] == "completed_with_errors"
    named = {key for row in payload["errors"] for key in row}
    assert named == {"llm_calls_failed", "llm_invalid_results"}


def test_recoverable_retries_are_not_reported_as_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    path = run_logger.save(16, "pd", "done", "gigachat", "", ["том.pdf"], [], {"requests": 10, "retries": 3, "rate_limits": 2, "errors": 0})
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["errors"] == []
    assert payload["technical_status"] == "completed"


def test_explicit_errors_are_kept_alongside_the_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    path = run_logger.save(17, "compliance", "error", "gigachat", "", ["том.pdf"], ["рд.pdf"], {"errors": 1}, errors=[{"run": "связь не прошла"}])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert {"run": "связь не прошла"} in payload["errors"]
    assert any("llm_calls_failed" in row for row in payload["errors"])
