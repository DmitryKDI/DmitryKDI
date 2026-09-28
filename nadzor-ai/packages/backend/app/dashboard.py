"""Дашборд инспектора (ТЗ 7, модуль 7).

По каждому объекту — последний процесс проверки и цвет:

  красный — есть подтверждённые инспектором нарушения;
  жёлтый  — есть кандидаты без решения, неполные доказательства, ошибка или
            проверка ещё идёт: объект требует внимания;
  зелёный — проверка завершена, нарушений не подтверждено и открытых
            кандидатов нет.

«Зелёный» не означает «нарушений нет»: это «по выполненной проверке
подтверждённых нарушений нет и работы для инспектора не осталось».
Фильтры — по разделам (разделу параметра матрицы), статусам и датам.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from . import auth, models, official_api
from .db import get_session

router = APIRouter(prefix="/api/v1", tags=["dashboard"])

GREEN, YELLOW, RED = "green", "yellow", "red"


def _color(body: dict) -> str:
    proto = body["protocol"]
    if proto["tables"]["confirmed_violations"]:
        return RED
    if (body["status"] in {"queued", "running", "error"} or proto["pending_candidates"]
            or proto["missing_evidence"]):
        return YELLOW
    return GREEN


def _sections(body: dict) -> set[str]:
    return {item.get("section") for item in (body.get("result") or {}).get("checks") or []
            if item.get("section") and item.get("finding_status") in {
                "CANDIDATE", "CONFIRMED_VIOLATION", "SUSPICION"}}


@router.get("/dashboard", summary="Объекты с цветовой индикацией (модуль 7)",
            dependencies=[Depends(auth.require(*auth.READERS))])
def dashboard(section: str | None = None, status: str | None = None,
              color: str | None = Query(None, pattern="^(green|yellow|red)$"),
              date_from: dt.date | None = None, date_to: dt.date | None = None,
              db: Session = Depends(get_session)):
    latest: dict[str, models.OfficialRun] = {}
    for run in db.query(models.OfficialRun).order_by(models.OfficialRun.id).all():
        latest[run.object_id] = run
    objects, sections = [], set()
    for object_id, run in sorted(latest.items()):
        body = official_api._run_dict(db, run)
        proto = body["protocol"]
        found = _sections(body)
        sections |= found
        row = {
            "object_id": object_id, "process_id": run.id, "color": _color(body),
            "status": body["process_status"], "scenario": proto["scenario"],
            "created_at": run.created_at.isoformat(),
            "finalized_at": body["finalized_at"],
            "confirmed": len(proto["tables"]["confirmed_violations"]),
            "pending_candidates": len(proto["pending_candidates"]),
            "suspicions": len(proto["tables"]["suspicions"]),
            "missing_evidence": len(proto["missing_evidence"]),
            "sections": sorted(found), "sync_status": body["sync_status"],
            "new_documents": len(body["pending_documents"]),
        }
        day = run.created_at.date()
        if ((section and section not in found) or (status and row["status"] != status)
                or (color and row["color"] != color)
                or (date_from and day < date_from) or (date_to and day > date_to)):
            continue
        objects.append(row)
    return {"objects": objects, "sections": sorted(sections),
            "totals": {name: sum(item["color"] == name for item in objects)
                       for name in (GREEN, YELLOW, RED)}}
