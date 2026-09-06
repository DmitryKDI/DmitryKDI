#!/usr/bin/env python3
"""Краткая сводка требований и способов производства работ по файлу(ам)
ПД — БЕЗ сравнения с РД, без графа маршрутизации, без триангуляции.

Г.80 — прямая правка схемы работы: `registry_diff.py --kind requirements
--verify-requirements` всё равно прогонял шаги сверки с РД (семантическая
проверка Г.49 по тексту РД, эскалация в зрение по листу РД, Г.33/35) даже
когда `--after` дан тем же файлом, что `--before` — реального РД на этом
этапе работы попросту нет. Эти шаги не просто бесполезны без настоящего
РД — они жрут основную часть лимита запросов (Г.78: сотни-полторы тысячи
вызовов на один раздел с полусотней требований, именно они упирались в
429). Здесь — только извлечение (форма 1/2 + форма 3) и ЛЛМ-фильтр шума
(Г.69/70), больше ничего: то, что реально нужно на этой стадии.

Запуск:
    python scripts/summarize_requirements.py --pd том1.pdf [--pd том2.pdf ...] \
        --provider gigachat --api-key ВАШ_КЛЮЧ [--out summary.txt]

Несколько `--pd` — как несколько `--before` в registry_diff.py: тексты
всех файлов объединяются в один корпус перед извлечением (годится, когда
один том раскидан по нескольким PDF-частям — не наш нынешний случай с
ООС8.1-8.4, там это РАЗНЫЕ тома, каждый гоняется отдельным запуском
скрипта, не через несколько --pd разом)."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.classification import classify_document, open_pdf  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.requirement_registry import (  # noqa: E402
    extract_general_requirements,
    render_requirements_summary,
)
from app.set_overview import official_section_label  # noqa: E402
from registry_diff import (  # noqa: E402
    _PROVIDER_ENV_KEY,
    _emit_general_requirements,
    _extract_requirements_llm_visible,
    _load_text_facts,
)


def _identity_line(path: str) -> str:
    """Одна строка «что это за файл» — раздел и число страниц, БЕЗ числа
    помещений/оборудования (Г.81: этот скрипт про текстовые требования,
    счётчики помещений из другой, не запущенной здесь механики создавали
    ложное впечатление, что регистры помещений — приоритет)."""
    classification = classify_document(path, Path(path).name)
    label = official_section_label(classification.discipline_code)
    code = f" [{classification.discipline_code}]" if classification.discipline_code else ""
    try:
        doc = open_pdf(path)
        try:
            pages = doc.page_count
        finally:
            doc.close()
    except Exception:  # noqa: BLE001 — не смогли открыть, но это не повод не показать хоть имя файла
        pages = "?"
    return f"  {Path(path).name} — {label}{code} ({classification.source}), {pages} стр."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pd", action="append", required=True, help="Файл(ы) ПД (можно несколько раз)")
    parser.add_argument("--provider", default="gigachat", choices=["anthropic", "gigachat"])
    parser.add_argument(
        "--api-key", default="",
        help="Ключ провайдера — без него смысла в этом скрипте нет (фильтр только через ИИ). "
             f"Если не передан, берётся из переменной окружения по провайдеру "
             f"({', '.join(_PROVIDER_ENV_KEY.values())}), как в registry_diff.py.",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out", default="", help="Дублировать вывод в файл по мере готовности (Г.41), не только в конце")
    args = parser.parse_args()

    # Г.82 (независимый аудит Opus, критическая находка №1) — раньше
    # несуществующий путь в --pd тихо давал чистый отчёт "0 требований" с
    # exit=0, неотличимый от реального результата "в документе нет
    # требований". Явная ошибка вместо правдоподобного, но лживого отчёта.
    missing = [p for p in args.pd if not Path(p).is_file()]
    if missing:
        sys.exit("ОШИБКА: файл(ы) --pd не найдены, отчёт НЕ построен (не путать с "
                  "«в документе нет требований»):\n" + "\n".join(f"  {p}" for p in missing))

    # Г.82 (находка №2) — без этого фолбэка `CURRENT-TASK.md`/`vision-keys.env`
    # (GIGACHAT_CREDENTIALS, GIGACHAT_CA_BUNDLE) не имели никакого эффекта на
    # этот скрипт: --api-key был обязательным аргументом без чтения окружения,
    # в отличие от registry_diff.py (см. _PROVIDER_ENV_KEY, строка ~1023 там).
    api_key = args.api_key or os.environ.get(_PROVIDER_ENV_KEY.get(args.provider, ""), "")
    if not api_key:
        sys.exit(
            f"ОШИБКА: нет ключа провайдера «{args.provider}» — передайте --api-key ИЛИ "
            f"задайте переменную окружения {_PROVIDER_ENV_KEY.get(args.provider, '?')} "
            f"(например, через vision-keys.env)."
        )

    llm_config = LlmConfig(provider=args.provider, api_key=api_key, base_url=args.base_url, model=args.model)

    out_f = open(args.out, "a", encoding="utf-8") if args.out else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        # Г.81 — прямое указание пользователя: требования — то, зачем
        # вообще запущен этот скрипт, идут ПЕРВЫМИ. Всё остальное (какой
        # это файл, сколько страниц) — только справочный контекст, чтобы
        # соотнести требования с объёмом тома, не самостоятельный
        # результат — печатается ПОСЛЕ, не перед требованиями.
        pd_text_facts = _load_text_facts(args.pd)

        _emit("=== Требования с кодом (форма 1/2, Г.32/36) ===")
        pd_requirements = _extract_requirements_llm_visible(pd_text_facts, llm_config, _emit)
        _emit(render_requirements_summary(pd_requirements))
        _emit("")

        _emit("=== Общий каталог требований и способов работы (форма 3, Г.47, отфильтрован ЛЛМ Г.69/70) ===")
        general_requirements = extract_general_requirements(pd_text_facts)
        _emit_general_requirements(general_requirements, llm_config, _emit)

        _emit("")
        _emit("=== Справка: обработанный(е) файл(ы) ===")
        for path in args.pd:
            _emit(_identity_line(path))
    finally:
        if out_f:
            out_f.close()


if __name__ == "__main__":
    main()
