"""Ежедневная проверка целостности хранилища (ТЗ 13, п.8; 12, п.7).

Сверяет контрольную сумму каждого оригинала и файла кэша с его отпечатком.
Расхождение — событие безопасности уровня ERROR в журнале и строка в
результате проверки; метрика `inspector_integrity_failures` даёт повод для
алерта. Документ с повреждённым оригиналом не «чинится» молча.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from . import file_store, models
from .db import SessionLocal
from .observability import logger

CHECK_PERIOD_HOURS = 24


def run_check() -> dict:
    with SessionLocal() as db:
        row = models.IntegrityCheck(started_at=dt.datetime.utcnow())
        db.add(row)
        db.commit()
        try:
            checked, failures = file_store.verify_all()
        except Exception as exc:  # noqa: BLE001 — сбой проверки виден, а не выдан за «всё цело»
            row.status, row.finished_at = "ERROR", dt.datetime.utcnow()
            row.failures = [{"digest": "", "reason": f"проверка не выполнена: {exc}"}]
            db.commit()
            logger.error(f"проверка целостности не выполнена: {exc}",
                         extra={"event": "integrity_check", "security": True})
            return check_dict(row)
        row.checked = checked
        row.failures = [{"digest": key, "reason": reason} for key, reason in failures]
        row.status = "FAILED" if failures else "OK"
        row.finished_at = dt.datetime.utcnow()
        db.commit()
        for key, reason in failures:
            logger.error(f"нарушена целостность файла {key}: {reason}",
                         extra={"event": "integrity_failure", "security": True})
        return check_dict(row)


def due(now: dt.datetime | None = None) -> bool:
    """Запустить проверку, если с прошлой прошли сутки. True — проверка выполнена."""
    now = now or dt.datetime.utcnow()
    with SessionLocal() as db:
        last = db.query(models.IntegrityCheck).order_by(models.IntegrityCheck.id.desc()).first()
        if last is not None and now - last.started_at < dt.timedelta(hours=CHECK_PERIOD_HOURS):
            return False
    run_check()
    return True


def last_failures(db: Session) -> int:
    last = (db.query(models.IntegrityCheck).filter(models.IntegrityCheck.finished_at.isnot(None))
            .order_by(models.IntegrityCheck.id.desc()).first())
    return len(last.failures or []) if last is not None else 0


def check_dict(row: models.IntegrityCheck) -> dict:
    return {"id": row.id, "status": row.status, "checked": row.checked,
            "failures": row.failures or [],
            "started_at": row.started_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at else None}
