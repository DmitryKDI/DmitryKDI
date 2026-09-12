"""Сводка по прогонам собирается из логов и не требует провайдера.

Поводом стал реальный разрыв: логгер писал в один каталог, а разбор
диагностики читал другой, и `runtime_summary` всегда выходил пустым. Пустая
сводка выглядела как «прогонов не было», хотя прогоны были (Г.10). Тест
закрывает именно это: подложенные логи должны попасть в сводку.

Обращения к модели здесь нет — проверяется только сборка сводки из файлов,
поэтому тест не нуждается в ключе и ничего не отправляет наружу.
"""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "gigachat_peer_review.py"


@pytest.fixture
def peer_review():
    spec = importlib.util.spec_from_file_location("gigachat_peer_review", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_runtime_summary_is_not_empty_for_existing_logs(
    peer_review, tmp_path, monkeypatch
):
    monkeypatch.setenv("NADZOR_RUN_LOGS_DIR", str(tmp_path))
    _write(tmp_path / "11_20250101T000000000000Z.json", {
        "run_id": 11,
        "run_type": "pd",
        "status": "done",
        "technical_status": "completed",
        "execution_state": "completed",
        "metrics": {"requests": 4, "responses": 4, "errors": 0},
        "errors": [],
        "pd_stage": {"requirements_total": 7, "failed_chunks": 0},
    })
    _write(tmp_path / "tasks" / "triangulated" / "latest.json", {
        "run_id": 11,
        "run_type": "triangulated",
        "status": "done",
        "technical_status": "completed_with_errors",
        "execution_state": "completed",
        "metrics": {"requests": 2, "elapsed_seconds": 31.5},
        "details": {"valid": True, "metrics_scope": "run"},
        "errors": [{"llm": "timeout"}],
    })

    summary = peer_review.collect_runtime_summary()

    assert summary["selected_root"] == str(tmp_path)
    assert "warning" not in summary
    assert summary["run_ids_all_equal"] is True
    assert summary["stages"]["pd"]["technical_status"] == "completed"
    assert summary["stages"]["pd"]["metrics"]["requests"] == 4
    assert summary["stages"]["pd"]["pd_stage"]["requirements_total"] == 7
    assert summary["stages"]["triangulated"]["technical_status"] == (
        "completed_with_errors"
    )
    assert summary["stages"]["triangulated"]["errors_count"] == 1


def test_runtime_summary_reports_absence_instead_of_staying_silent(
    peer_review, tmp_path, monkeypatch
):
    """Нет логов — так и должно быть сказано, а не «всё в порядке»."""
    monkeypatch.setenv("NADZOR_RUN_LOGS_DIR", str(tmp_path / "empty"))

    summary = peer_review.collect_runtime_summary()

    assert summary["selected_root"] is None
    assert summary["warning"]
    assert summary["stages"] == {}


def test_logger_directory_is_among_the_roots_that_are_read(
    peer_review, tmp_path, monkeypatch
):
    """Читаем ровно тот каталог, в который пишет логгер."""
    from app import run_logger

    monkeypatch.delenv("NADZOR_RUN_LOGS_DIR", raising=False)
    roots = peer_review._runtime_root_candidates()

    assert run_logger.RUN_LOGS_DIR in roots
    assert roots[0] == run_logger.RUN_LOGS_DIR
