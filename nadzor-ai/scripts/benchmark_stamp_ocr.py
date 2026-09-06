#!/usr/bin/env python3
"""Замер качества распознавания штампа на реальных листах РД.

Зачем: без чтения штампа не работает верхний уровень сопоставления
(CLAUDE.md, Г.5), а у РД штамп в кривых — текстом читается 69 страниц из
712. Вопрос «потянет ли локальная модель роль OCR» нельзя решить чтением
бенчмарков: OCRBench меряет английский и китайский, а нам нужен русский
штамп по ГОСТ с табличной разметкой. Поэтому — прямой замер на эталоне.

Эталон ниже прочитан вручную с тех же самых листов: это не выдумка и не
разметка «на глаз по смыслу», а буквальное содержимое граф.

Запуск (локальная Ollama):
    python scripts/benchmark_stamp_ocr.py \
        --ov1 "путь/АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf" \
        --ov21 "путь/АНО-150321-1-РД-ОВ2.1_изм. 3_в1.pdf"

Другой провайдер для сравнения:  --provider anthropic --api-key …
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

import pymupdf  # noqa: E402

from app.llm import LlmConfig  # noqa: E402
from app.stamp_vision import read_stamp_ocr  # noqa: E402

# (файл, страница) -> (номер листа, наименование, шифр)
GROUND_TRUTH = {
    ("ov1", 14): (1, "Общие данные", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 15): (2, "Характеристика отопительно-вентиляционных систем (начало)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 16): (3, "Характеристика отопительно-вентиляционных систем (окончание)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 17): (4, "План подвала (вентиляция)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 18): (5, "План 1-го этажа (вентиляция)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 19): (6, "План 2-го этажа (вентиляция)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 20): (7, "План 3-го этажа (вентиляция)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 21): (8, "План кровли (вентиляция)", "АНО/150321/1-РД-ОВ1"),
    ("ov1", 22): (9, "План подвала (кондиционирование)", "АНО/150321/1-РД-ОВ1"),
    ("ov21", 15): (2, "План подвала (отопление и теплоснабжение)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 16): (3, "План 1-го этажа (отопление)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 17): (4, "План 2-го этажа (отопление)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 19): (6, "Принципиальная схема системы отопления (начало)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 20): (7, "Принципиальная схема системы отопления (продолжение №1)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 21): (8, "Принципиальная схема системы отопления (продолжение №2)", "АНО/150321/1-РД-ОВ2.1"),
    ("ov21", 22): (9, "Принципиальная схема системы отопления (окончание)", "АНО/150321/1-РД-ОВ2.1"),
}

_NORM = re.compile(r"[^а-яёa-z0-9]+")


def _words(text: str) -> set:
    return {w for w in _NORM.split((text or "").lower()) if w}


def name_score(got: str, want: str) -> float:
    """Доля слов эталона, попавших в ответ. Точное совпадение строки требовать
    нельзя: «(вентиляция)» и «(Вентиляция)» — один и тот же лист."""
    want_words = _words(want)
    return len(_words(got) & want_words) / len(want_words) if want_words else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ov1", required=True)
    ap.add_argument("--ov21", required=True)
    ap.add_argument("--provider", default="local")
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--api-key", default="")
    args = ap.parse_args()

    config = LlmConfig(provider=args.provider, api_key=args.api_key,
                       model=args.model, base_url=args.base_url)
    docs = {"ov1": pymupdf.open(args.ov1), "ov21": pymupdf.open(args.ov21)}

    no_ok = name_ok = shifr_ok = failed = 0
    scores, elapsed = [], []
    for (tag, page_no), (want_no, want_name, want_shifr) in GROUND_TRUTH.items():
        started = time.monotonic()
        try:
            got = read_stamp_ocr(docs[tag][page_no - 1], config)
        except Exception as exc:  # noqa: BLE001 — один сорванный лист не должен ронять замер
            print(f"{tag} стр.{page_no}: ОШИБКА {exc}")
            failed += 1
            continue
        elapsed.append(time.monotonic() - started)

        score = name_score(got.sheet_name or "", want_name)
        scores.append(score)
        num_hit = got.sheet_no == want_no
        shifr_hit = want_shifr.lower().replace("/", "").replace("-", "") in \
            (got.shifr or "").lower().replace("/", "").replace("-", "")
        no_ok += num_hit
        name_ok += score >= 0.7
        shifr_ok += shifr_hit
        mark = "✓" if (num_hit and score >= 0.7) else "✗"
        print(f"{mark} {tag} стр.{page_no:3d}: лист {str(got.sheet_no):>4s} (эталон {want_no:2d})  "
              f"совпадение названия {score:4.0%}  «{(got.sheet_name or '—')[:52]}»")

    total = len(scores)
    if not total:
        print("\nни один лист не распознан — проверьте, что модель доступна")
        return
    print(f"\n--- итог по {total} листам ({failed} сорвалось) ---")
    print(f"номер листа верно:      {no_ok:3d}/{total}  ({100*no_ok/total:.0f}%)")
    print(f"наименование (≥70%):    {name_ok:3d}/{total}  ({100*name_ok/total:.0f}%)")
    print(f"шифр верно:             {shifr_ok:3d}/{total}  ({100*shifr_ok/total:.0f}%)")
    print(f"среднее совпадение названия: {100*sum(scores)/total:.0f}%")
    print(f"время на лист: {sum(elapsed)/len(elapsed):.1f} с  "
          f"→ на 712 листов РД ≈ {sum(elapsed)/len(elapsed)*712/60:.0f} мин")


if __name__ == "__main__":
    main()
