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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.llm import LlmConfig  # noqa: E402
from app.requirement_registry import (  # noqa: E402
    extract_general_requirements,
    render_requirements_summary,
)
from app.set_overview import render_volume_summary, summarize_set  # noqa: E402
from registry_diff import (  # noqa: E402
    _emit_general_requirements,
    _extract_requirements_llm_visible,
    _load_text_facts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pd", action="append", required=True, help="Файл(ы) ПД (можно несколько раз)")
    parser.add_argument("--provider", default="gigachat", choices=["anthropic", "gigachat"])
    parser.add_argument("--api-key", required=True, help="Ключ провайдера — без него смысла в этом скрипте нет (фильтр только через ИИ)")
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out", default="", help="Дублировать вывод в файл по мере готовности (Г.41), не только в конце")
    parser.add_argument("--no-overview", action="store_true", help="Не печатать обзор тома в начале")
    args = parser.parse_args()

    llm_config = LlmConfig(provider=args.provider, api_key=args.api_key, base_url=args.base_url, model=args.model)

    out_f = open(args.out, "a", encoding="utf-8") if args.out else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        if not args.no_overview:
            # Только сторона ПД — никакого "РД/ИД" и никакого сравнения
            # разделов ПД<->РД (Г.80: РД на этом этапе нет вообще, а
            # run_overview печатал бы фиктивную "сторону РД", повторяющую
            # ПД саму по себе, что и порождало путаницу "ищет сравнение с
            # РД, хотя её нет").
            _emit(render_volume_summary(summarize_set(args.pd), "ПД"))
            _emit("")

        pd_text_facts = _load_text_facts(args.pd)

        _emit("=== Требования с кодом (форма 1/2, Г.32/36) ===")
        pd_requirements = _extract_requirements_llm_visible(pd_text_facts, llm_config, _emit)
        _emit(render_requirements_summary(pd_requirements))
        _emit("")

        _emit("=== Общий каталог требований и способов работы (форма 3, Г.47, отфильтрован ЛЛМ Г.69/70) ===")
        general_requirements = extract_general_requirements(pd_text_facts)
        _emit_general_requirements(general_requirements, llm_config, _emit)
    finally:
        if out_f:
            out_f.close()


if __name__ == "__main__":
    main()
