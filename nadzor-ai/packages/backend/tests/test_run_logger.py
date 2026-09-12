import json
from datetime import datetime, timezone
from pathlib import Path

from app import run_logger


def test_save_task_snapshot_writes_immutable_log_and_latest_mirror(tmp_path, monkeypatch):
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
    assert path.name.startswith("17_")
    assert path.name.endswith(".json")
    latest = path.parent / "latest.json"
    assert latest.exists()

    payload = json.loads(path.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload == latest_payload
    assert payload["run_type"] == "analysis"
    assert payload["run_id"] == 17
    assert payload["status"] == "done"
    assert payload["technical_status"] == "completed"
    assert payload["execution_state"] == "completed"
    assert payload["execution_id"] == "analysis:17"
    assert payload["documents_before"] == ["pd.pdf"]
    assert payload["documents_after"] == ["rd.pdf"]
    assert payload["details"]["pairs_total"] == 5
    assert payload["metrics"]["calls"] == 5


def test_task_log_preserves_previous_run_and_moves_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", tmp_path)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")

    first = run_logger.save_task_snapshot(run_type="analysis", run_id=1, status="done")
    second = run_logger.save_task_snapshot(run_type="analysis", run_id=2, status="error")

    assert first != second
    assert first.exists()
    assert second.exists()
    folder = tmp_path / "tasks" / "analysis"
    latest = folder / "latest.json"
    files = sorted(folder.glob("*.json"))
    assert len(files) == 3
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["run_id"] == 2
    assert payload["status"] == "error"
    assert payload["technical_status"] == "failed"


def test_task_log_paths_do_not_collide_between_run_types(tmp_path, monkeypatch):
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", tmp_path / "tasks")
    when = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)

    analysis, analysis_latest = run_logger._task_log_paths("analysis", 1, when)
    triangulated, triangulated_latest = run_logger._task_log_paths("triangulated", 1, when)

    assert analysis != triangulated
    assert analysis.parent.name == "analysis"
    assert triangulated.parent.name == "triangulated"
    assert analysis.name.startswith("1_")
    assert triangulated.name.startswith("1_")
    assert analysis_latest.name == "latest.json"
    assert triangulated_latest.name == "latest.json"
    assert analysis_latest != triangulated_latest


def test_stage_logs_preserve_history_across_same_run_type(tmp_path, monkeypatch):
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

    assert first_pd.exists()
    assert compliance.exists()
    assert second_pd.exists()
    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in tmp_path.glob("*.json")
    ]
    assert sorted(payload["run_type"] for payload in payloads) == [
        "compliance",
        "pd",
        "pd",
    ]
    pd_ids = sorted(
        payload["run_id"] for payload in payloads if payload["run_type"] == "pd"
    )
    assert pd_ids == [1, 2]


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
            "confirmed": [{"key": "A"}],
            "candidates": [{"key": "B"}, {"key": "C"}],
        },
        "verdicts": [{"key": "A"}],
        "escalation_tickets": [{"key": "B"}],
    })

    assert compact["rooms"]["findings_total"] == 1
    assert compact["equipment"]["findings_total"] == 2
    assert compact["triangulation"]["confirmed_total"] == 1
    assert compact["triangulation"]["candidates_total"] == 2
    assert compact["verdicts_total"] == 1
    assert compact["escalation_tickets_total"] == 1
    assert "findings" not in compact["rooms"]


def test_run_logs_dir_lives_in_project_data_not_inside_packages():
    """Логи прогонов должны лежать там же, где их ищет разбор диагностики.

    Раньше путь считался на один уровень выше нужного, каталог оказывался
    внутри `packages/`, и сводка по прогонам всегда выходила пустой — при том
    что прогоны были. Проверяем отношением к корню проекта, а не строкой:
    при переносе каталога проекта тест должен остаться верным.
    """
    project_root = Path(run_logger.__file__).resolve().parents[3]

    assert run_logger.RUN_LOGS_DIR == project_root / "data" / "run_logs"
    assert run_logger.RUN_LOGS_DIR.parent == project_root / "data"
    assert "packages" not in run_logger.RUN_LOGS_DIR.relative_to(project_root).parts


def test_find_run_logs_reads_legacy_directory_and_keeps_chronology(
    tmp_path, monkeypatch
):
    """Прогоны из устаревшего каталога не должны исчезнуть после обновления."""
    current = tmp_path / "current"
    legacy = tmp_path / "legacy"
    current.mkdir()
    legacy.mkdir()
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", current)
    monkeypatch.setattr(run_logger, "LEGACY_RUN_LOGS_DIR", legacy)

    old = legacy / "7_20240101T000000000000Z.json"
    new = current / "7_20250101T000000000000Z.json"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    (current / "8_20250101T000000000000Z.json").write_text("{}", encoding="utf-8")

    found = run_logger.find_run_logs(7)

    assert found == [old, new]
    assert found[-1] == new
