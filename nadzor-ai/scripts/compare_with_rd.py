#!/usr/bin/env python3
"""СТАДИЯ 2 — сверка разобранной ПД с рабочей документацией.

Г.87 — отдельная стадия по прямому решению пользователя: сверка запускается
не вместе с разбором, а после него, по СОХРАНЁННОМУ результату стадии 1.
Два следствия, ради которых так и сделано:

  - ПД не разбирается заново на каждую сверку. Разбор тома — это десятки
    вызовов ЛЛМ; повторять их ради сверки бессмысленно и дорого.
  - Стадия 1 остаётся самодостаточной. Её результат — законченный продукт
    сам по себе, а не полуфабрикат: РД нет и не будет на половине
    документов, и это норма, а не ошибка.

Запуск:
    # что уже разобрано
    python scripts/compare_with_rd.py --list

    # сверить разбор №5 с рабочей документацией
    python scripts/compare_with_rd.py --run 5 --rd РД.pdf [--rd РД2.pdf ...] \
        --provider gigachat --api-key ВАШ_КЛЮЧ

    # взять последний разбор конкретного тома
    python scripts/compare_with_rd.py --for-document "Том ООС8.1.pdf" --rd РД.pdf
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.llm import LlmConfig  # noqa: E402
from app.matching import DocumentInput  # noqa: E402
from app.pd_store import latest_run_id, list_runs, load_run  # noqa: E402
from app.requirement_cross_check import (  # noqa: E402
    cross_check_general_requirements,
    cross_check_requirements,
    render_general_requirement_cross_check_report,
    render_requirement_cross_check_report,
)
from app.requirement_text_verify import (  # noqa: E402
    render_text_verify_report,
    verify_general_requirements_llm,
)
from registry_diff import _PROVIDER_ENV_KEY, _load_text_facts  # noqa: E402


def _print_runs() -> None:
    runs = list_runs()
    if not runs:
        print("Разборов ПД пока нет. Сначала: python scripts/summarize_requirements.py --pd <файл>")
        return
    print("Разборы ПД (последние сверху):")
    for r in runs:
        docs = ", ".join(r["documents"]) or "—"
        sections = ", ".join(r["sections"]) or "раздел не определён"
        warn = f"  ВНИМАНИЕ: {r['failed_chunks']} пачек ЛЛМ упало" if r["failed_chunks"] else ""
        print(f"  №{r['id']}  {r['created_at']}  [{r['extractor']}]  {sections}  "
              f"требований: {r['requirements_total']}  {docs}{warn}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Показать разборы ПД и выйти")
    parser.add_argument("--run", type=int, default=0, help="Номер разбора ПД (из --list)")
    parser.add_argument("--for-document", default="", help="Взять последний разбор этого файла")
    parser.add_argument("--rd", action="append", default=[], help="Файл(ы) рабочей документации")
    parser.add_argument("--provider", default="gigachat", choices=["anthropic", "gigachat"])
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.list:
        _print_runs()
        return

    # Какой разбор берём. Г.10: не найдено — явная ошибка, а не пустая
    # сверка, которая выглядела бы как «расхождений нет».
    run_id = args.run or latest_run_id(args.for_document or None)
    if not run_id:
        target = f" для «{args.for_document}»" if args.for_document else ""
        sys.exit(f"ОШИБКА: разбор ПД{target} не найден. Сначала выполните разбор:\n"
                 f"  python scripts/summarize_requirements.py --pd <файл ПД>\n"
                 f"Список уже сделанных разборов: --list")

    requirements = load_run(run_id)
    if not requirements:
        sys.exit(f"ОШИБКА: в разборе №{run_id} нет ни одного требования — сверять нечего. "
                 f"Это не «расхождений нет»: разбор дал пустой результат, и сверка "
                 f"по нему ничего не значила бы.")

    if not args.rd:
        sys.exit("ОШИБКА: не задан --rd. Стадия сверки без рабочей документации не имеет смысла; "
                 "если РД ещё нет — это нормально, просто пользуйтесь результатом стадии 1.")

    missing = [p for p in args.rd if not Path(p).is_file()]
    if missing:
        sys.exit("ОШИБКА: файл(ы) --rd не найдены:\n" + "\n".join(f"  {p}" for p in missing))

    api_key = args.api_key or os.environ.get(_PROVIDER_ENV_KEY.get(args.provider, ""), "")
    llm_config = (
        LlmConfig(provider=args.provider, api_key=api_key, base_url=args.base_url, model=args.model)
        if api_key else None
    )

    # noqa: SIM115 — файл живёт весь прогон и закрывается в finally: вывод
    # пишется по мере готовности (Г.41), а не одним куском в конце.
    out_f = open(args.out, "a", encoding="utf-8") if args.out else None  # noqa: SIM115

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        _emit(f"=== Сверка разбора ПД №{run_id} с рабочей документацией ===")
        _emit(f"  требований из ПД: {len(requirements)}")
        _emit(f"  файлов РД: {len(args.rd)}")
        _emit("")

        rd_text_facts = _load_text_facts(args.rd)
        rd_side = [DocumentInput(name="РД", pages=1, text_facts=rd_text_facts)]

        # Требования с привязкой к помещению — вход сверки по номеру.
        # Г.86: фильтрация здесь, у того, чей это контракт, а не в
        # извлечении, где это было бы потерей данных.
        room_bound = [r for r in requirements if r.rooms]
        _emit(f"=== Сверка по номеру помещения ({len(room_bound)} из {len(requirements)} требований) ===")
        if room_bound:
            _emit(render_requirement_cross_check_report(cross_check_requirements(room_bound, rd_side)))
        else:
            _emit("  Ни одно требование не привязано к номеру помещения — сверка по номеру")
            _emit("  неприменима. Для разделов ООС/ПОС/ПБ это норма: требования там")
            _emit("  относятся к объекту целиком. Смотрите сверку по тексту ниже.")
        _emit("")

        _emit("=== Сверка по тексту: ГОСТ, марка, класс ===")
        general = cross_check_general_requirements(requirements, rd_side)
        _emit(render_general_requirement_cross_check_report(general))
        _emit("")

        if llm_config is None:
            _emit("=== Смысловая сверка с текстом РД — НЕ выполнялась ===")
            _emit("  Нужен ключ ЛЛМ: сверка идёт по смыслу, а не по совпадению символов.")
            _emit("  Передайте --api-key или задайте переменную окружения.")
            return

        confirmed = {f.sentence_pd for f in general.findings
                     if f.finding_type == "token_confirmed_in_rd"}
        pending = [r for r in requirements if r.sentence not in confirmed]
        _emit(f"=== Смысловая сверка с текстом РД ({len(pending)} нерешённых требований) ===")
        results = verify_general_requirements_llm(
            pending, rd_text_facts, llm_config,
            on_result=lambda r: _emit(f"  [{r['verdict']}] стр.{r['page']}: {r['reason']}"),
        )
        _emit("")
        _emit(render_text_verify_report(results))
    finally:
        if out_f:
            out_f.close()


if __name__ == "__main__":
    main()
