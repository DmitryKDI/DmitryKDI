"""Фоновые задачи сервиса.

  • повтор передачи во внешнюю систему по расписанию 1, 5, 15 минут (ТЗ 9.6);
  • еженедельный отчёт для ML-инженеров (ТЗ 7, модуль 10).

Один поток, который раз в POLL_S секунд проверяет сроки. Сбой задачи
печатается и не останавливает цикл: следующая проверка — по расписанию.
"""
from __future__ import annotations

import datetime as dt
import threading

from . import external_sync, feedback, models
from .db import SessionLocal

POLL_S = 30
REPORT_PERIOD_DAYS = 7

_worker: threading.Thread | None = None
_stop = threading.Event()


def weekly_report_due(now: dt.datetime | None = None) -> bool:
    """Сформировать отчёт, если с конца прошлого прошла неделя. True — сформирован."""
    now = now or dt.datetime.utcnow()
    with SessionLocal() as db:
        last = (db.query(models.WeeklyReport)
                .order_by(models.WeeklyReport.period_end.desc()).first())
        if last is not None and now - last.period_end < dt.timedelta(days=REPORT_PERIOD_DAYS):
            return False
        payload = feedback.weekly_report(db, end=now, days=REPORT_PERIOD_DAYS)
        db.add(models.WeeklyReport(period_start=now - dt.timedelta(days=REPORT_PERIOD_DAYS),
                                   period_end=now, payload=payload))
        db.commit()
    return True


def run_once(now: dt.datetime | None = None) -> None:
    for job in (external_sync.retry_due, weekly_report_due):
        try:
            job(now)
        except Exception as exc:  # noqa: BLE001 — сбой задачи не должен останавливать цикл
            print(f"фоновая задача {job.__name__} не выполнена: {exc}")


def start() -> None:
    """Запустить цикл фоновых задач; повторный вызов ничего не делает."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    _stop.clear()

    def loop() -> None:
        while not _stop.wait(POLL_S):
            run_once()

    _worker = threading.Thread(target=loop, name="background-jobs", daemon=True)
    _worker.start()


def stop() -> None:
    _stop.set()
