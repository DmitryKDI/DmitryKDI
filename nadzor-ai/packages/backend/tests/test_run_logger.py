import json
from datetime import datetime, timezone

from app import run_logger


def test_save_task_snapshot_writes_latest_typed_log(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")

    path = run_logger.save_task_snapshot(
        run_type="analysis",
        run_id=17,
        status="done",
        provider="gigachat",
        model="GigaChat-3-Ultra",
        documents_before=["pd.pdf"],
        documents_after=["rd.pdf"],
        metrics={"calls": 5},
        details={"pairs_total": 5, "findings_total": 2},
        errors=[],
    )

    assert path.parent == tmp_path / "tasks" / "analysis"
    assert path.name == "latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_type"] == "analysis"
    assert payload["run_id"] == 17
    assert payload["status"] == "done"
    assert payload["documents_before"] == ["pd.pdf"]
    assert payload["documents_after"] == ["rd.pdf"]
    assert payload["details"]["pairs_total"] == 5
    assert payload["metrics"]["calls"] == 5


def test_task_log_overwrites_previous_run_of_same_type(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")

    first = run_logger.save_task_snapshot(run_type="analysis", run_id=1, status="done")
    second = run_logger.save_task_snapshot(run_type="analysis", run_id=2, status="error")

    assert first == second
    files = list((tmp_path / "tasks" / "analysis").glob("*.json"))
    assert files == [second]
    payload = json.loads(second.read_text(encoding="utf-8"))
    assert payload["run_id"] == 2
    assert payload["status"] == "error"


def test_task_log_paths_do_not_collide_between_run_types(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")
    when = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)

    analysis = run_logger._task_log_path("analysis", 1, when)
    triangulated = run_logger._task_log_path("triangulated", 1, when)

    assert analysis != triangulated
    assert analysis.parent.name == "analysis"
    assert triangulated.parent.name == "triangulated"
    assert analysis.name == "latest.json"
    assert triangulated.name == "latest.json"


def test_stage_log_replaces_only_same_run_type(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")

    first_pd = run_logger.save(
        run_id=1,
        run_type="pd",
        status="done",
        provider="gigachat",
        model="",
        documents_before=["pd-v1.pdf"],
        documents_after=[],
        metrics={},
    )
    # У разных типов прогонов независимые пространства run_id. Используем
    # разные id, чтобы этот тест проверял именно очистку по run_type, а не
    # случайную коллизию имени файла с точностью timestamp до секунды.
    compliance = run_logger.save(
        run_id=77,
        run_type="compliance",
        status="done",
        provider="gigachat",
        model="",
        documents_before=["pd-v1.pdf"],
        documents_after=["rd.pdf"],
        metrics={},
    )
    second_pd = run_logger.save(
        run_id=2,
        run_type="pd",
        status="done",
        provider="gigachat",
        model="",
        documents_before=["pd-v2.pdf"],
        documents_after=[],
        metrics={},
    )

    assert not first_pd.exists()
    assert compliance.exists()
    assert second_pd.exists()
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in tmp_path.glob("*.json")]
    assert sorted(payload["run_type"] for payload in payloads) == ["compliance", "pd"]
    pd_payload = next(payload for payload in payloads if payload["run_type"] == "pd")
    assert pd_payload["run_id"] == 2
    assert pd_payload["documents_before"] == ["pd-v2.pdf"]


def test_compact_triangulated_result_keeps_quality_diagnostics_only():
    compact = run_logger._compact_triangulated_result({
        "valid": True,
        "skipped_files": [],
        "llm": {"used": True, "call_failures": []},
        "not_run": ["some_optional_step"],
        "rooms": {
            "total_pd": 10,
            "total_rd": 12,
            "matched": 9,
            "unmatched": 4,
            "findings": [{"detail": "large raw finding"}],
        },
        "equipment": {
            "total_pd": 3,
            "total_rd": 4,
            "matched": 2,
            "unmatched": 3,
            "findings": [{"detail": "raw"}, {"detail": "raw2"}],
        },
        "requirements": {"coded": {"total": 2}},
        "triangulation": {
            "signals_count": 8,
            "confirmed": [{"key": "267"}],
            "candidates": [{"key": "012"}, {"key": "140"}],
        },
        "verdicts": [{"key": "267"}],
        "escalation_tickets": [{"key": "012"}],
    })

    assert compact["rooms"]["findings_total"] == 1
    assert compact["equipment"]["findings_total"] == 2
    assert compact["triangulation"]["confirmed_total"] == 1
    assert compact["triangulation"]["candidates_total"] == 2
    assert compact["verdicts_total"] == 1
    assert compact["escalation_tickets_total"] == 1
    assert "findings" not in compact["rooms"]
