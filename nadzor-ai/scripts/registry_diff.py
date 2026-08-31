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

Проверка кандидатов картинкой (без сети, только против уже указанной LLM —
локальной по умолчанию):
  python scripts/registry_diff.py --before ПД.pdf --after РД.pdf --verify
  python scripts/registry_diff.py --before ПД.pdf --after РД.pdf --verify --provider gigachat --model GigaChat-2-Pro --api-key "$KEY"
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.documents import extract_document_facts  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.vision import render_page_to_png_bytes, verify_candidate  # noqa: E402

_KIND_ATTR = {"rooms": "room_facts", "equipment": "equipment_facts"}
_KIND_LABEL = {"rooms": "помещений", "equipment": "оборудования"}


def _registry(paths: list[str], fact_attr: str) -> dict[str, list[dict]]:
    """key -> [{name, doc, path, page}] по всем страницам всех файлов одной
    стороны.

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
            reg[fact["key"]].append({"name": fact["name"], "doc": name, "path": str(p), "page": fact["page"]})
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


def _verify_group(title: str, keys: set, registry: dict[str, list[dict]], kind: str, config: LlmConfig) -> None:
    """Прогоняет каждого кандидата через verify_candidate() — по картинке
    первого листа, где он встретился. Не сравнение (сравнивать здесь не с
    чем — ключ по определению есть только с одной стороны), а триаж:
    реальная позиция реестра или шум извлечения (Г.28)."""
    print(f"\n{title} ({len(keys)}) — проверка ИИ:")
    for key in sorted(keys):
        entry = registry[key][0]
        try:
            png = render_page_to_png_bytes(entry["path"], entry["page"])
            result = verify_candidate(png, key, kind, config)
        except Exception as exc:  # noqa: BLE001 — сбой одной проверки не должен ронять остальные
            print(f"  {key}\tОШИБКА: {exc}")
            continue
        real = result.get("real")
        mark = "РЕАЛЬНО" if real else ("ШУМ" if real is False else "?")
        print(f"  {key}\t[{mark}]\t{result.get('reason', '')}")


def run(before_paths: list[str], after_paths: list[str], kind: str, config: Optional[LlmConfig]) -> None:
    attr = _KIND_ATTR[kind]
    before = _registry(before_paths, attr)
    after = _registry(after_paths, attr)
    both, only_before, only_after = _diff(before, after)
    label = _KIND_LABEL[kind]
    print(f"\n=== Реестр {label}: ПД {len(before)} записей, РД {len(after)} записей ===")
    _print_group("Есть с обеих сторон", both, before)
    if config is None:
        _print_group("Только в ПД — кандидат «отсутствует в РД»", only_before, before)
        _print_group("Только в РД — кандидат «добавлено, не было в ПД»", only_after, after)
    else:
        _verify_group("Только в ПД — кандидат «отсутствует в РД»", only_before, before, kind, config)
        _verify_group("Только в РД — кандидат «добавлено, не было в ПД»", only_after, after, kind, config)


def main() -> None:
    parser = argparse.ArgumentParser(description="Алгоритмический diff реестров ПД/РД (без LLM, опционально с триажем ИИ)")
    parser.add_argument("--before", action="append", required=True, help="PDF стороны ПД (можно несколько раз)")
    parser.add_argument("--after", action="append", required=True, help="PDF стороны РД/ИД (можно несколько раз)")
    parser.add_argument("--kind", choices=["rooms", "equipment", "both"], default="both")
    parser.add_argument("--verify", action="store_true",
                        help="Прогнать кандидатов «только с одной стороны» через LLM (по одной картинке) — реальная позиция или шум извлечения")
    parser.add_argument("--provider", default="gigachat", choices=["openai", "anthropic", "google", "yandexgpt", "gigachat"])
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    config = LlmConfig(provider=args.provider, api_key=args.api_key, base_url=args.base_url, model=args.model) if args.verify else None

    kinds = ["rooms", "equipment"] if args.kind == "both" else [args.kind]
    for kind in kinds:
        run(args.before, args.after, kind, config)


if __name__ == "__main__":
    main()
