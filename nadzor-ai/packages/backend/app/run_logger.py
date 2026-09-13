"""Automatic JSON diagnostics for background analysis tasks.

Runtime logging is intentionally latest-only.  Historical timestamped snapshots
used to accumulate indefinitely and made local diagnosis noisy and easy to mix
across runs.  The logger now keeps one top-level file per run type and one
``latest.json`` per background task type.  Old timestamped snapshots are purged
on import and are not created by new runs.

Background LLM metrics are measured per run, not copied from process-wide
counters, so provider queue time and request timing remain attributable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def default_run_logs_dir() -> Path:
    """Return the canonical runtime log directory."""
    return _PROJECT_ROOT / "data" / "run_logs"


RUN_LOGS_DIR = Path(os.environ.get("NADZOR_RUN_LOGS_DIR", default_run_logs_dir()))
TASK_LOGS_DIR = RUN_LOGS_DIR / "tasks"
LEGACY_RUN_LOGS_DIR = Path(__file__).resolve().parents[2] / "data" / "run_logs"


def _ensure_dir(path: Path | None = None) -> None:
    (path or RUN_LOGS_DIR).mkdir(parents=True, exist_ok=True)


def _safe_run_type(run_type: str) -> str:
    return "".join(ch for ch in str(run_type) if ch.isalnum() or ch in ("-", "_")) or "task"


def _log_path(run_id: int, timestamp: datetime, run_type: str | None = None) -> Path:
    """Stable latest-only top-level path.

    ``run_id`` and ``timestamp`` remain in the signature for compatibility with
    older tests/callers; retention is keyed by run type, not by timestamp.
    """
    del run_id, timestamp
    return RUN_LOGS_DIR / f"{_safe_run_type(run_type or 'run')}_latest.json"


def _task_log_paths(run_type: str, run_id: int, timestamp: datetime) -> tuple[Path, Path]:
    """Return the single retained task snapshot twice for compatibility."""
    del run_id, timestamp
    folder = TASK_LOGS_DIR / _safe_run_type(run_type)
    _ensure_dir(folder)
    latest = folder / "latest.json"
    return latest, latest


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    _ensure_dir(path.parent)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temp.replace(path)
    return path


def _purge_old_logs(root: Path) -> None:
    """Remove obsolete immutable snapshots while preserving latest-only files."""
    if not root.is_dir():
        return
    for path in root.glob("*.json"):
        if path.name.endswith("_latest.json") or path.name == "latest.json":
            continue
        try:
            path.unlink()
        except OSError:
            pass
    tasks = root / "tasks"
    if tasks.is_dir():
        for folder in tasks.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                if path.name == "latest.json":
                    continue
                try:
                    path.unlink()
                except OSError:
                    pass


_FAILURE_COUNTERS = {
    "errors": "llm_calls_failed",
    "invalid_results": "llm_invalid_results",
}


def errors_from_metrics(metrics: dict | None) -> list[dict]:
    rows: list[dict] = []
    for counter, name in _FAILURE_COUNTERS.items():
        try:
            count = int(float((metrics or {}).get(counter) or 0))
        except (TypeError, ValueError):
            continue
        if count > 0:
            rows.append({name: count})
    return rows


def _technical_status(status: str, errors: list[dict] | None = None, details: dict | None = None) -> str:
    normalized = str(status or "").strip().casefold()
    errors = list(errors or [])
    details = dict(details or {})
    if details.get("valid") is False:
        return "technical_invalid"
    if errors:
        if normalized in {"done", "completed", "success", "succeeded"}:
            return "completed_with_errors"
        return "failed"
    if normalized in {"done", "completed", "success", "succeeded"}:
        return "completed"
    if normalized in {"error", "failed", "failure"}:
        return "failed"
    if normalized in {"queued", "pending"}:
        return "queued"
    if normalized in {"running", "processing", "started", "in_progress"}:
        return "running"
    return normalized or "unknown"


def _execution_state(technical_status: str) -> str:
    if technical_status in {"completed", "completed_with_errors"}:
        return "completed"
    if technical_status in {"failed", "technical_invalid"}:
        return "failed"
    if technical_status == "queued":
        return "queued"
    if technical_status == "running":
        return "started"
    return "unknown"


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
    _ensure_dir()
    timestamp = datetime.now(timezone.utc)
    error_rows = list(errors or []) + errors_from_metrics(metrics)
    technical_status = _technical_status(status, error_rows)
    payload: dict[str, Any] = {
        "run_id": run_id,
        "run_type": run_type,
        "execution_id": f"{_safe_run_type(run_type)}:{int(run_id)}",
        "timestamp": timestamp.isoformat(),
        "status": status,
        "technical_status": technical_status,
        "execution_state": _execution_state(technical_status),
        "provider": provider,
        "model": model,
        "documents_before": documents_before,
        "documents_after": documents_after,
        "metrics": metrics,
        "errors": error_rows,
    }
    if pd_stage is not None:
        payload["pd_stage"] = pd_stage
    if compliance is not None:
        payload["compliance"] = compliance
    return _write_json(_log_path(run_id, timestamp, run_type), payload)


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
    timestamp = datetime.now(timezone.utc)
    detail_rows = dict(details or {})
    error_rows = list(errors or []) + errors_from_metrics(metrics)
    technical_status = _technical_status(status, error_rows, detail_rows)
    payload = {
        "run_id": int(run_id),
        "run_type": run_type,
        "execution_id": f"{_safe_run_type(run_type)}:{int(run_id)}",
        "timestamp": timestamp.isoformat(),
        "status": status,
        "technical_status": technical_status,
        "execution_state": _execution_state(technical_status),
        "provider": provider,
        "model": model,
        "documents_before": list(documents_before or []),
        "documents_after": list(documents_after or []),
        "metrics": dict(metrics or {}),
        "details": detail_rows,
        "errors": error_rows,
    }
    latest, _ = _task_log_paths(run_type, run_id, timestamp)
    return _write_json(latest, payload)


def _document_names(db, ids: list[int] | None) -> list[str]:
    from . import models
    names: list[str] = []
    for document_id in ids or []:
        doc = db.get(models.Document, document_id)
        if doc is not None:
            names.append(doc.name)
    return names


def _short(value: object, limit: int = 600) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _snapshot_analysis(run_id: int, metrics: dict | None = None) -> None:
    from . import models
    from .db import get_session
    from .llm_runtime import PROCESS_METRICS

    db = next(get_session())
    try:
        run = db.get(models.AnalysisRun, run_id)
        if run is None:
            return
        pairs = db.query(models.PagePair).filter(models.PagePair.run_id == run_id).order_by(models.PagePair.id).all()
        findings = db.query(models.Finding).filter(models.Finding.run_id == run_id).order_by(models.Finding.id).all()
        pair_errors = [
            {
                "pair_id": pair.id,
                "before_document_id": pair.before_document_id,
                "before_page": pair.before_page,
                "after_document_id": pair.after_document_id,
                "after_page": pair.after_page,
                "matched_by": pair.matched_by,
                "score": pair.score,
                "error": pair.llm_error,
            }
            for pair in pairs
            if pair.llm_status == "error" or pair.llm_error
        ]
        errors = list(pair_errors)
        if run.error:
            errors.append({"run": run.error})
        pair_map = {pair.id: pair for pair in pairs}
        finding_rows = []
        for finding in findings:
            pair = pair_map.get(finding.pair_id)
            finding_rows.append({
                "id": finding.id,
                "pair_id": finding.pair_id,
                "kind": finding.kind,
                "label": _short(finding.label, 160),
                "change": _short(finding.change_text),
                "severity": finding.severity,
                "field_check": _short(finding.field_check, 300),
                "before_page": pair.before_page if pair else None,
                "after_page": pair.after_page if pair else None,
                "score": pair.score if pair else None,
            })
        save_task_snapshot(
            run_type="analysis",
            run_id=run_id,
            status=run.status or "unknown",
            provider=run.provider or "",
            model=run.model or "",
            documents_before=_document_names(db, run.before_document_ids),
            documents_after=_document_names(db, run.after_document_ids),
            metrics=metrics or PROCESS_METRICS.snapshot(),
            details={
                "metrics_scope": "run" if metrics is not None else "process",
                "pair_score_semantics": "routing_similarity_not_difference_probability",
                "pairs_total": run.pairs_total or 0,
                "pairs_done": run.pairs_done or 0,
                "pairs_llm_ok": run.pairs_llm_ok or 0,
                "pairs_llm_error": run.pairs_llm_error or 0,
                "findings_total": len(findings),
                "findings": finding_rows,
                "pairs": [
                    {
                        "pair_id": pair.id,
                        "before_document_id": pair.before_document_id,
                        "before_page": pair.before_page,
                        "after_document_id": pair.after_document_id,
                        "after_page": pair.after_page,
                        "matched_by": pair.matched_by,
                        "page_kind": pair.page_kind,
                        "score": pair.score,
                        "discipline_mismatch": bool(pair.discipline_mismatch),
                        "llm_status": pair.llm_status,
                    }
                    for pair in pairs
                ],
            },
            errors=errors,
        )
    finally:
        db.close()


def _compact_visual_results(block: object) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    results = []
    kept_keys = {
        "rooms", "sentence", "verdict", "reason", "where", "pages_checked",
        "page", "document", "status", "execution_state", "pair_key",
        "before_page", "after_page", "before_file_index", "after_file_index",
        "score", "routing_score", "diff_ratio", "changed_cells", "local_cluster", "hot_zone",
        "significant_total", "rooms_shared", "rooms_mentioned", "anchors_shared",
        "changes", "error", "semantic_path", "region_id", "anchor_type",
        "anchor_ids", "anchor_provenance", "pair_confidence", "comparability",
        "pd_clip", "rd_clip", "image_layout", "general_max_dim", "local_max_dim",
        "verification_executed", "calls_used", "calls_budget", "proposals_total",
        "regions_discovered", "regions_verified", "discovery_first",
        "whole_page_done", "region_discovery_done", "candidate_regions_total",
        "candidate_regions_checked", "min_local_checks_for_no_change",
        "differences_total", "semantic_architecture", "evidence_complete",
        "pd_inventory", "rd_inventory", "confirmed_findings", "unverified_candidates",
        "coverage_notes", "uncertainties", "errors",
    }
    for item in block.get("results") or []:
        if not isinstance(item, dict):
            continue
        results.append({
            key: (_short(value) if key in {"sentence", "reason"} else value)
            for key, value in item.items()
            if key in kept_keys
        })
    out = {key: value for key, value in block.items() if key != "results"}
    out["results"] = results
    return out


def _compact_confirmations(items: object, limit: int = 80) -> list[dict]:
    out: list[dict] = []
    for item in (items or [])[:limit]:
        if isinstance(item, dict):
            out.append({
                "domain": item.get("domain"),
                "key": item.get("key"),
                "status": item.get("status"),
                "sources": item.get("sources") or [],
                "details": [_short(value, 400) for value in item.get("details") or []],
            })
    return out


def _compact_triangulated_result(result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    rooms = result.get("rooms") if isinstance(result.get("rooms"), dict) else {}
    equipment = result.get("equipment") if isinstance(result.get("equipment"), dict) else {}
    requirements = result.get("requirements") if isinstance(result.get("requirements"), dict) else {}
    triangulation = result.get("triangulation") if isinstance(result.get("triangulation"), dict) else {}
    routing = result.get("routing") if isinstance(result.get("routing"), dict) else None
    return {
        "valid": result.get("valid"),
        "reason": result.get("reason"),
        "active_architecture": result.get("active_architecture"),
        "legacy_runtime": result.get("legacy_runtime") or {},
        "skipped_files": result.get("skipped_files") or [],
        "llm": result.get("llm") or {},
        "not_run": result.get("not_run") or [],
        "performance": result.get("performance") or {},
        "semantic_findings": result.get("semantic_findings") or [],
        "rooms": {
            "active": rooms.get("active"),
            "total_pd": rooms.get("total_pd"),
            "total_rd": rooms.get("total_rd"),
            "matched": rooms.get("matched"),
            "unmatched": rooms.get("unmatched"),
            "findings_total": len(rooms.get("findings") or []),
            "signals_total": rooms.get("signals_total"),
        },
        "equipment": {
            "active": equipment.get("active"),
            "total_pd": equipment.get("total_pd"),
            "total_rd": equipment.get("total_rd"),
            "matched": equipment.get("matched"),
            "unmatched": equipment.get("unmatched"),
            "findings_total": len(equipment.get("findings") or []),
            "signals_total": equipment.get("signals_total"),
        },
        "requirements": requirements,
        "vision_requirements": _compact_visual_results(result.get("vision_requirements")),
        "pair_vision": _compact_visual_results(result.get("pair_vision")),
        "routing": {
            "room_keys": routing.get("room_keys") or [],
            "auto_selected": bool(routing.get("auto_selected")),
        } if routing else None,
        "triangulation": {
            "active": triangulation.get("active"),
            "signals_count": triangulation.get("signals_count"),
            "confirmed_total": len(triangulation.get("confirmed") or []),
            "candidates_total": len(triangulation.get("candidates") or []),
            "confirmed": _compact_confirmations(triangulation.get("confirmed")),
            "candidates": _compact_confirmations(triangulation.get("candidates")),
        },
        "verdicts_total": len(result.get("verdicts") or []),
        "escalation_tickets_total": len(result.get("escalation_tickets") or []),
    }


def _snapshot_triangulated(run_id: int, metrics: dict | None = None) -> None:
    from . import models
    from .db import get_session
    from .llm_runtime import PROCESS_METRICS

    db = next(get_session())
    try:
        run = db.get(models.TriangulatedRun, run_id)
        if run is None:
            return
        result = run.result if isinstance(run.result, dict) else {}
        errors: list[dict] = []
        if run.error:
            errors.append({"run": run.error})
        llm = result.get("llm") if isinstance(result.get("llm"), dict) else {}
        for failure in llm.get("call_failures") or []:
            errors.append({"llm": str(failure)})
        save_task_snapshot(
            run_type="triangulated",
            run_id=run_id,
            status=run.status or "unknown",
            provider=run.provider or "",
            documents_before=_document_names(db, run.before_document_ids),
            documents_after=_document_names(db, run.after_document_ids),
            metrics=metrics or PROCESS_METRICS.snapshot(),
            details={
                "metrics_scope": "run" if metrics is not None else "process",
                **_compact_triangulated_result(result),
            },
            errors=errors,
        )
    finally:
        db.close()


_AUTO_LOG_TASKS: dict[str, Callable[..., None]] = {
    "_run_analysis": _snapshot_analysis,
    "_run_triangulated": _snapshot_triangulated,
}
_BACKGROUND_PATCHED = False


def install_background_task_logging() -> None:
    global _BACKGROUND_PATCHED
    if _BACKGROUND_PATCHED:
        return

    from starlette.background import BackgroundTasks
    from .llm_runtime import measure_run

    original_add_task = BackgroundTasks.add_task

    @wraps(original_add_task)
    def add_task_with_logging(self, func, *args, **kwargs):
        snapshot = _AUTO_LOG_TASKS.get(getattr(func, "__name__", ""))
        if snapshot is None:
            return original_add_task(self, func, *args, **kwargs)

        @wraps(func)
        def logged_task(*task_args, **task_kwargs):
            measured = None
            try:
                with measure_run(getattr(func, "__name__", "task")) as measured:
                    return func(*task_args, **task_kwargs)
            finally:
                try:
                    raw_run_id = task_args[0] if task_args else task_kwargs.get("run_id")
                    if raw_run_id is not None:
                        snapshot(
                            int(raw_run_id),
                            metrics=(measured.snapshot() if measured is not None else None),
                        )
                except Exception as exc:  # noqa: BLE001
                    print(f"автолог фоновой задачи не записан: {type(exc).__name__}: {exc}")

        return original_add_task(self, logged_task, *args, **kwargs)

    BackgroundTasks.add_task = add_task_with_logging
    _BACKGROUND_PATCHED = True


# Clean up immutable snapshots created by previous versions before registering
# background logging.  New runs never create them again.
_purge_old_logs(RUN_LOGS_DIR)
if LEGACY_RUN_LOGS_DIR != RUN_LOGS_DIR:
    _purge_old_logs(LEGACY_RUN_LOGS_DIR)

install_background_task_logging()
