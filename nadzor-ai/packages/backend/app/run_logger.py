"""Запись диагностических логов фоновых прогонов в JSON.

Разбор ПД и compliance вызывают :func:`save` из своих ``finally``.
Прямое сравнение листов и триангуляция логируются автоматически через
обёртку Starlette ``BackgroundTasks``.

Логи предназначены для отладки качества и производительности. В них нет
текста PDF, содержимого файлов или ключей провайдера: только метаданные
прогона, счётчики, ошибки и агрегированные результаты.

Чтобы каталог не разрастался после каждого запуска, хранится только
последний лог каждого типа:

* ``data/run_logs/tasks/analysis/latest.json``
* ``data/run_logs/tasks/triangulated/latest.json``
* в корне ``data/run_logs`` — только последний ``pd`` и последний
  ``compliance`` лог; старый лог того же типа удаляется перед записью.

Запись выполняется атомарно через временный файл и ``replace``: читатель
никогда не увидит наполовину записанный JSON.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

# Путь к каталогу логов — рядом с базой данных, в data/run_logs/.
RUN_LOGS_DIR = Path(__file__).resolve().parents[2] / "data" / "run_logs"
TASK_LOGS_DIR = RUN_LOGS_DIR / "tasks"


def _ensure_dir(path: Path | None = None) -> None:
    """Создать каталог логов, если его ещё нет."""
    (path or RUN_LOGS_DIR).mkdir(parents=True, exist_ok=True)


def _safe_run_type(run_type: str) -> str:
    return "".join(ch for ch in str(run_type) if ch.isalnum() or ch in ("-", "_")) or "task"


def _log_path(run_id: int, timestamp: datetime) -> Path:
    """Совместимое имя stage-лога; старые логи того же типа чистит save()."""
    ts_str = timestamp.strftime("%Y%m%dT%H%M%SZ")
    return RUN_LOGS_DIR / f"{run_id}_{ts_str}.json"


def _task_log_path(run_type: str, run_id: int, timestamp: datetime) -> Path:
    """Путь единственного актуального лога фоновой задачи данного типа."""
    del run_id, timestamp  # имя стабильно: новый прогон заменяет предыдущий
    folder = TASK_LOGS_DIR / _safe_run_type(run_type)
    _ensure_dir(folder)
    return folder / "latest.json"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    """Атомарно заменить JSON, не оставляя частично записанный файл."""
    _ensure_dir(path.parent)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temp.replace(path)
    return path


def _remove_previous_stage_logs(run_type: str) -> None:
    """Оставить в корне только один лог каждого stage-типа.

    Старый HTTP API ищет ПД/compliance по шаблону ``{run_id}_*.json``,
    поэтому имена этих файлов пока сохраняем совместимыми. Перед новым
    сохранением удаляем только JSON с тем же ``run_type``; лог соседней
    стадии не затрагивается.
    """
    _ensure_dir()
    for path in RUN_LOGS_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if payload.get("run_type") == run_type:
            try:
                path.unlink()
            except OSError:
                pass


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
    """Записать последний лог стадии ПД/compliance на диск.

    Новый запуск того же ``run_type`` удаляет предыдущий лог этого типа.
    """
    _ensure_dir()
    timestamp = datetime.now(timezone.utc)
    _remove_previous_stage_logs(run_type)
    path = _log_path(run_id, timestamp)

    log_data: dict[str, Any] = {
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
    return _write_json(path, log_data)


def save_task_snapshot(
    *,
    run_type: str,
    run_id: int,
    status: str,
    provider: str = "",
    model: str = "",
    documents_before: list[str] | None = None,
    documents_after: list[str] | None = None,
    metrics: dict | None = None,
    details: dict | None = None,
    errors: list[dict] | None = None,
) -> Path:
    """Перезаписать последний автоматический лог фоновой задачи."""
    timestamp = datetime.now(timezone.utc)
    path = _task_log_path(run_type, run_id, timestamp)
    payload: dict[str, Any] = {
        "run_id": int(run_id),
        "run_type": run_type,
        "timestamp": timestamp.isoformat(),
        "status": status,
        "provider": provider,
        "model": model,
        "documents_before": list(documents_before or []),
        "documents_after": list(documents_after or []),
        "metrics": dict(metrics or {}),
        "details": dict(details or {}),
        "errors": list(errors or []),
    }
    return _write_json(path, payload)


def _document_names(db, ids: list[int] | None) -> list[str]:
    from . import models

    names: list[str] = []
    for document_id in ids or []:
        doc = db.get(models.Document, document_id)
        if doc is not None:
            names.append(doc.name)
    return names


def _snapshot_analysis(run_id: int) -> None:
    """Снять итог прямого сравнения после закрытия фоновой задачи."""
    from . import models
    from .db import get_session
    from .llm_runtime import PROCESS_METRICS

    db = next(get_session())
    try:
        run = db.get(models.AnalysisRun, run_id)
        if run is None:
            return
        pairs = (db.query(models.PagePair)
                 .filter(models.PagePair.run_id == run_id)
                 .order_by(models.PagePair.id).all())
        findings_total = (db.query(models.Finding)
                          .filter(models.Finding.run_id == run_id).count())
        pair_errors = [
            {
                "pair_id": p.id,
                "before_document_id": p.before_document_id,
                "before_page": p.before_page,
                "after_document_id": p.after_document_id,
                "after_page": p.after_page,
                "matched_by": p.matched_by,
                "score": p.score,
                "error": p.llm_error,
            }
            for p in pairs if p.llm_status == "error" or p.llm_error
        ]
        errors = list(pair_errors)
        if run.error:
            errors.append({"run": run.error})
        save_task_snapshot(
            run_type="analysis",
            run_id=run_id,
            status=run.status or "unknown",
            provider=run.provider or "",
            model=run.model or "",
            documents_before=_document_names(db, run.before_document_ids),
            documents_after=_document_names(db, run.after_document_ids),
            metrics=PROCESS_METRICS.snapshot(),
            details={
                "metrics_scope": "process",
                "pairs_total": run.pairs_total or 0,
                "pairs_done": run.pairs_done or 0,
                "pairs_llm_ok": run.pairs_llm_ok or 0,
                "pairs_llm_error": run.pairs_llm_error or 0,
                "findings_total": findings_total,
                "pairs": [
                    {
                        "pair_id": p.id,
                        "before_document_id": p.before_document_id,
                        "before_page": p.before_page,
                        "after_document_id": p.after_document_id,
                        "after_page": p.after_page,
                        "matched_by": p.matched_by,
                        "page_kind": p.page_kind,
                        "score": p.score,
                        "discipline_mismatch": bool(p.discipline_mismatch),
                        "llm_status": p.llm_status,
                    }
                    for p in pairs
                ],
            },
            errors=errors,
        )
    finally:
        db.close()


def _compact_triangulated_result(result: object) -> dict[str, Any]:
    """Убрать объёмные сырые списки, оставив диагностику качества прогона."""
    if not isinstance(result, dict):
        return {}
    rooms = result.get("rooms") if isinstance(result.get("rooms"), dict) else {}
    equipment = result.get("equipment") if isinstance(result.get("equipment"), dict) else {}
    requirements = result.get("requirements") if isinstance(result.get("requirements"), dict) else {}
    triangulation = result.get("triangulation") if isinstance(result.get("triangulation"), dict) else {}
    return {
        "valid": result.get("valid"),
        "reason": result.get("reason"),
        "skipped_files": result.get("skipped_files") or [],
        "llm": result.get("llm") or {},
        "not_run": result.get("not_run") or [],
        "rooms": {
            "total_pd": rooms.get("total_pd"),
            "total_rd": rooms.get("total_rd"),
            "matched": rooms.get("matched"),
            "unmatched": rooms.get("unmatched"),
            "findings_total": len(rooms.get("findings") or []),
        },
        "equipment": {
            "total_pd": equipment.get("total_pd"),
            "total_rd": equipment.get("total_rd"),
            "matched": equipment.get("matched"),
            "unmatched": equipment.get("unmatched"),
            "findings_total": len(equipment.get("findings") or []),
        },
        "requirements": requirements,
        "triangulation": {
            "signals_count": triangulation.get("signals_count"),
            "confirmed_total": len(triangulation.get("confirmed") or []),
            "candidates_total": len(triangulation.get("candidates") or []),
        },
        "verdicts_total": len(result.get("verdicts") or []),
        "escalation_tickets_total": len(result.get("escalation_tickets") or []),
    }


def _snapshot_triangulated(run_id: int) -> None:
    """Снять итог триангуляции после закрытия фоновой задачи."""
    from . import models
    from .db import get_session
    from .llm_runtime import PROCESS_METRICS

    db = next(get_session())
    try:
        run = db.get(models.TriangulatedRun, run_id)
        if run is None:
            return
        errors: list[dict] = []
        if run.error:
            errors.append({"run": run.error})
        result = run.result if isinstance(run.result, dict) else {}
        call_failures = ((result.get("llm") or {}).get("call_failures")
                         if isinstance(result.get("llm"), dict) else None)
        for failure in call_failures or []:
            errors.append({"llm": str(failure)})
        save_task_snapshot(
            run_type="triangulated",
            run_id=run_id,
            status=run.status or "unknown",
            provider=run.provider or "",
            documents_before=_document_names(db, run.before_document_ids),
            documents_after=_document_names(db, run.after_document_ids),
            metrics=PROCESS_METRICS.snapshot(),
            details={"metrics_scope": "process", **_compact_triangulated_result(result)},
            errors=errors,
        )
    finally:
        db.close()


_AUTO_LOG_TASKS: dict[str, Callable[[int], None]] = {
    "_run_analysis": _snapshot_analysis,
    "_run_triangulated": _snapshot_triangulated,
}
_BACKGROUND_PATCHED = False


def install_background_task_logging() -> None:
    """Автоматически логировать фоновые analysis/triangulated задачи."""
    global _BACKGROUND_PATCHED
    if _BACKGROUND_PATCHED:
        return

    from starlette.background import BackgroundTasks

    original_add_task = BackgroundTasks.add_task

    @wraps(original_add_task)
    def add_task_with_logging(self, func, *args, **kwargs):
        snapshot = _AUTO_LOG_TASKS.get(getattr(func, "__name__", ""))
        if snapshot is None:
            return original_add_task(self, func, *args, **kwargs)

        @wraps(func)
        def logged_task(*task_args, **task_kwargs):
            try:
                return func(*task_args, **task_kwargs)
            finally:
                try:
                    raw_run_id = task_args[0] if task_args else task_kwargs.get("run_id")
                    if raw_run_id is not None:
                        snapshot(int(raw_run_id))
                except Exception as exc:  # noqa: BLE001 — логирование никогда не ломает сам прогон
                    print(f"автолог фоновой задачи не записан: {type(exc).__name__}: {exc}")

        return original_add_task(self, logged_task, *args, **kwargs)

    BackgroundTasks.add_task = add_task_with_logging
    _BACKGROUND_PATCHED = True


# main.py импортирует run_logger при старте приложения — этого достаточно,
# чтобы все последующие постановки analysis/triangulated задач получили лог.
install_background_task_logging()
