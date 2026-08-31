#!/usr/bin/env python3
"""Алгоритмический diff реестров помещений/оборудования ПД<->РД — без LLM.

Три категории по Г.9 (есть с обеих сторон / только ПД / только РД) для
реестра помещений (rooms.py) и реестра оборудования (equipment.py, Г.20),
посчитанные сопоставлением ключей по ВСЕМ страницам всего комплекта сразу —
не по одной выбранной паре листов, а по полному тексту документов.

Работает только с локальными PDF: не обращается к сети, не вызывает LLM,
не исполняет ничего из содержимого документов. Названия, извлечённые из
документов — данные из материала, предоставленного поднадзорным лицом
(модель угроз У-1, см. vision.py), скрипт их только сравнивает и печатает.

Запуск:
  python scripts/registry_diff.py --before ПД1.pdf --before ПД2.pdf --after РД1.pdf --after РД2.pdf
  python scripts/registry_diff.py --before ПД.pdf --after РД.pdf --kind equipment
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.documents import extract_document_facts  # noqa: E402

_KIND_ATTR = {"rooms": "room_facts", "equipment": "equipment_facts"}
_KIND_LABEL = {"rooms": "помещений", "equipment": "оборудования"}


def _registry(paths: list[str], fact_attr: str) -> dict[str, list[dict]]:
    """key -> [{name, doc, page}] по всем страницам всех файлов одной стороны.

    Файл, который не удалось прочитать (битый PDF, недоступный путь), не
    роняет весь прогон — пропускается с явным предупреждением на stderr, а
    не молча (Г.10)."""
    reg: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        p = Path(path)
        if not p.is_file():
            print(f"пропущен (не найден): {path}", file=sys.stderr)
            continue
        name = p.name
        try:
            facts = extract_document_facts(str(p), name)
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
            print(f"пропущен ({exc}): {path}", file=sys.stderr)
            continue
        for fact in getattr(facts, fact_attr):
            reg[fact["key"]].append({"name": fact["name"], "doc": name, "page": fact["page"]})
    return reg


def _diff(before: dict, after: dict) -> tuple[set, set, set]:
    b, a = set(before), set(after)
    return b & a, b - a, a - b


def _print_group(title: str, keys: set, registry: dict[str, list[dict]]) -> None:
    print(f"\n{title} ({len(keys)}):")
    for key in sorted(keys):
        entries = registry[key]
        first = entries[0]
        where = ", ".join(f"{e['doc']}#{e['page']}" for e in entries[:3])
        more = f" (+{len(entries) - 3})" if len(entries) > 3 else ""
        print(f"  {key}\t{first['name']!r}\t[{where}{more}]")


def run(before_paths: list[str], after_paths: list[str], kind: str) -> None:
    attr = _KIND_ATTR[kind]
    before = _registry(before_paths, attr)
    after = _registry(after_paths, attr)
    both, only_before, only_after = _diff(before, after)
    label = _KIND_LABEL[kind]
    print(f"\n=== Реестр {label}: ПД {len(before)} записей, РД {len(after)} записей ===")
    _print_group("Есть с обеих сторон", both, before)
    _print_group("Только в ПД — кандидат «отсутствует в РД»", only_before, before)
    _print_group("Только в РД — кандидат «добавлено, не было в ПД»", only_after, after)


def main() -> None:
    parser = argparse.ArgumentParser(description="Алгоритмический diff реестров ПД/РД (без LLM)")
    parser.add_argument("--before", action="append", required=True, help="PDF стороны ПД (можно несколько раз)")
    parser.add_argument("--after", action="append", required=True, help="PDF стороны РД/ИД (можно несколько раз)")
    parser.add_argument("--kind", choices=["rooms", "equipment", "both"], default="both")
    args = parser.parse_args()

    kinds = ["rooms", "equipment"] if args.kind == "both" else [args.kind]
    for kind in kinds:
        run(args.before, args.after, kind)


if __name__ == "__main__":
    main()
