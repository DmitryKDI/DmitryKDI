#!/usr/bin/env python3
"""Что программа делает прямо сейчас — по всем прогонам.

Отвечает на вопрос «работает или зависло», не открывая браузер и не трогая
работающий сервер: база читается напрямую. Полезно, когда интерфейс
показывает «идёт», а понять, на каком шаге, больше негде (Г.116).

    .venv/bin/python scripts/runs.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app import models  # noqa: E402
from app.db import get_session  # noqa: E402

KINDS = (
    ("разбор", models.PdRun),
    ("сверка", models.ComplianceRun),
    ("анализ", models.AnalysisRun),
    ("карта внимания", models.TriangulatedRun),
)


def _age(started: dt.datetime | None) -> str:
    if started is None:
        return "—"
    seconds = int((dt.datetime.utcnow() - started).total_seconds())
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"


def main() -> int:
    db = next(get_session())
    try:
        running = 0
        for label, model in KINDS:
            rows = db.query(model).order_by(model.id.desc()).limit(5).all()
            if not rows:
                continue
            print(f"\n=== {label} ===")
            for row in rows:
                stage = getattr(row, "stage", "") or ""
                done = getattr(row, "units_done", 0) or 0
                total = getattr(row, "units_total", 0) or 0
                started = getattr(row, "started_at", None)
                mark = "→" if row.status == "running" else " "
                if row.status == "running":
                    running += 1
                counter = f"{done}/{total}" if total else ""
                # «идёт» пишется только там, где есть от чего считать: у
                # прогона без отметки начала это слово было бы выдумкой.
                elapsed = f"идёт {_age(started)}" if started is not None else ""
                print(f" {mark} №{row.id}  {row.status:9} {counter:>9}  {elapsed:>16}  {stage}")
                if getattr(row, "error", None):
                    print(f"      причина: {row.error}")
        if running:
            print("\nПрогон в работе. Если этап не меняется дольше нескольких минут — это"
                  "\nобращение к модели, которое ещё не вернулось: остановить можно кнопкой"
                  "\n«Остановить» в интерфейсе, она сработает на ближайшей безопасной точке.")
        else:
            print("\nНи один прогон не выполняется.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
