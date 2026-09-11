"""Запись полного лога прогона на диск в JSON.

После завершения каждого этапа (done/error/cancelled) автоматически
записывает метрики GigaChat, результаты извлечения требований,
вердикты сверки и ошибки в JSON-файл.

Агенты могут прочитать лог через:
  GET /pd-runs/{id}/log
  GET /compliance-runs/{id}/log

Или напрямую из файловой системы:
  data/run_logs/{run_id}_{timestamp}.json

Файлы НЕ содержат текстов документов, ключей провайдера или
чувствительных данных объекта — только метрики, вердикты и
обобщённые результаты.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Путь к каталогу логов — рядом с базой данных, в data/run_logs/
RUN_LOGS_DIR = Path(__file__).resolve().parents[2] / "data" / "run_logs"


def _ensure_dir() -> None:
    """Создаёт каталог логов, если не существует."""
    RUN_LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _log_path(run_id: int, timestamp: datetime) -> Path:
    """Путь к файлу лога: data/run_logs/{run_id}_{timestamp}.json"""
    ts_str = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return RUN_LOGS_DIR / f"{run_id}_{ts_str}.json"


def save(
    run_id: int,
    run_type: str,
    status: str,
    provider: str,
    model: str,
    documents_before: list[str],
    documents_after: list[str],
    metrics: dict,
    pd_stage: dict | None = None,
    compliance: dict | None = None,
    errors: list[dict] | None = None,
) -> Path:
    """Записать лог прогона на диск.

    Аргументы:
        run_id: идентификатор прогона
        run_type: "pd" или "compliance"
        status: "done", "error" или "cancelled"
        provider: "gigachat"
        model: название модели
        documents_before: имена документов ПД
        documents_after: имена документов РД
        metrics: словарь метрик GigaChat (из Metrics.snapshot())
        pd_stage: данные разбора ПД (composition, requirements_total и т.д.)
        compliance: данные сверки (counts, verdicts и т.д.)
        errors: список ошибок с page/chunk/exception

    Возвращает:
        Path к сохранённому файлу лога
    """
    _ensure_dir()
    timestamp = datetime.now(timezone.utc)
    path = _log_path(run_id, timestamp)

    log_data = {
        "run_id": run_id,
        "run_type": run_type,
        "timestamp": timestamp.isoformat(),
        "status": status,
        "provider": provider,
        "model": model,
        "documents_before": documents_before,
        "documents_after": documents_after,
        "metrics": metrics,
    }

    if pd_stage is not None:
        log_data["pd_stage"] = pd_stage

    if compliance is not None:
        log_data["compliance"] = compliance

    if errors is not None:
        log_data["errors"] = errors

    with open(path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)

    return path
