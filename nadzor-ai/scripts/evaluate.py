"""Оценка процессов проверки по эталонной разметке (ТЗ 14; лист «Метрики»).

Запуск на стенде после проверки эталонных объектов:

    PYTHONPATH=packages/backend .venv/bin/python scripts/evaluate.py \\
        --reference /путь/вне/репозитория/gold.json --process-ids 1,2,3 \\
        --out /путь/вне/репозитория/отчёт.json

Эталон — данные заказчика: храните его вне репозитория. Код возврата 0 —
все метрики посчитаны и прошли пороги, 1 — есть непройденные или
непосчитанные (перечислены в отчёте).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--process-ids", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    from app import evaluation
    from app.db import SessionLocal

    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    ids = [int(part) for part in args.process_ids.split(",") if part.strip()]
    with SessionLocal() as db:
        report = evaluation.evaluate(db, reference, ids)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    for name, metric in report["metrics"].items():
        state = {True: "пройдено", False: "НЕ ПРОЙДЕНО", None: "не посчитано"}[metric["passed"]]
        print(f"{name:24} {metric['value']!s:>8}  n={metric['size']:<5} "
              f"ДИ95={metric['ci95']}  порог {metric['threshold']}  — {state}")
    print("ИТОГ:", "принято" if report["passed"] else "не принято")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
