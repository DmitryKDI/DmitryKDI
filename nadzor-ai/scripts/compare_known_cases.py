#!/usr/bin/env python3
"""A/B-проверка на трёх реальных известных нарушениях: сравнение листов
С подсказкой из data/known_violations.json и БЕЗ неё, на одних и тех же,
уже правильно найденных вручную парах листов ПД/РД.

Смысл: page-matching в packages/backend сейчас (см. CLAUDE.md) неверно
сопоставляет эти три листа на реальном объёме документов, поэтому пары
листов здесь заданы вручную — это тест промпта/модели, а не матчинга.

Запуск локально, с работающей Ollama (qwen2.5vl:7b по умолчанию):
    python scripts/compare_known_cases.py \
        --pd "PD/V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf" \
        --ov1 "RD/АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf" \
        --ov21 "RD/АНО-150321-1-РД-ОВ2.1_изм. 3_в1.pdf"

Провайдер/модель переопределяются: --provider local --model qwen2.5vl:7b
--base-url http://localhost:11434/v1 (или --provider anthropic/openai/google
с --api-key, если есть внешний ключ).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app import vision  # noqa: E402
from app.llm import LlmConfig  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pd", required=True, help="путь к тому ПД (5.4.2 ОВ)")
    ap.add_argument("--ov1", required=True, help="путь к РД-ОВ1 (часть 1-100)")
    ap.add_argument("--ov21", required=True, help="путь к РД-ОВ2.1")
    ap.add_argument("--provider", default="local")
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    # Пары листов найдены вручную по номерам помещений (grep по тексту РД),
    # это ЗАВЕДОМО правильные пары — тест не зависит от матчинга.
    cases = [
        ("Лист 26: венткамера 012 (форкамера/приточная установка)", args.pd, 104, args.ov1, 17),
        ("Лист 21: МГН-помещения 267/270/271/272 (тёплые полы)", args.pd, 99, args.ov21, 17),
        ("Лист 10: лаборатории 140-147 (2-3 этаж)", args.pd, 88, args.ov1, 18),
    ]

    config = LlmConfig(provider=args.provider, api_key=args.api_key, model=args.model, base_url=args.base_url)
    real_path = vision.KNOWN_VIOLATIONS_PATH
    no_hint_path = Path("/nonexistent-known-violations.json")

    results = {}
    for label, before_pdf, before_page, after_pdf, after_page in cases:
        for mode, path in (("with_hint", real_path), ("no_hint", no_hint_path)):
            vision.KNOWN_VIOLATIONS_PATH = path
            try:
                r = vision.compare_page_pair(before_pdf, before_page, after_pdf, after_page,
                                              config, context="раздел ОВ", discipline="ОВ")
            except Exception as e:  # сеть/модель — не должны ронять сравнение остальных пар
                r = {"error": repr(e)}
            results[f"{label} [{mode}]"] = r
            print(f"=== {label} [{mode}] ===")
            print(json.dumps(r, ensure_ascii=False, indent=2))
            print()

    vision.KNOWN_VIOLATIONS_PATH = real_path
    out_path = ROOT / "compare_known_cases_result.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Сохранено: {out_path}")


if __name__ == "__main__":
    main()
