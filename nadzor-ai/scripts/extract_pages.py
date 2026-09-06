#!/usr/bin/env python3
"""Вырезать отдельные страницы PDF в новый файл — для быстрой проверки
scan_cli.py на конкретных листах без прогона всего комплекта.

Запуск:
  python scripts/extract_pages.py ПД.pdf 88 99 104 -o pd_subset.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymupdf


def main() -> None:
    parser = argparse.ArgumentParser(description="Вырезать страницы PDF в новый файл")
    parser.add_argument("pdf", help="исходный PDF")
    parser.add_argument("pages", nargs="+", type=int, help="номера страниц (с 1), в любом порядке")
    parser.add_argument("-o", "--out", required=True, help="путь нового PDF")
    args = parser.parse_args()

    doc = pymupdf.open(args.pdf)
    out = pymupdf.open()
    for page in args.pages:
        if not (1 <= page <= doc.page_count):
            print(f"страница {page} вне диапазона (всего {doc.page_count})", file=sys.stderr)
            sys.exit(1)
        out.insert_pdf(doc, from_page=page - 1, to_page=page - 1)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out.save(args.out)
    print(f"{len(args.pages)} стр. -> {args.out}")


if __name__ == "__main__":
    main()
