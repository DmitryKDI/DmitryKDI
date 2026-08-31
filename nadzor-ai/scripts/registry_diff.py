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
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.documents import extract_document_facts  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.matching import DocumentInput  # noqa: E402
from app.requirement_cross_check import (  # noqa: E402
    cross_check_requirements,
    render_requirement_cross_check_report,
)
from app.vision import render_page_to_png_bytes, verify_candidate  # noqa: E402
from app.vision_page_compare import (  # noqa: E402
    check_predicate_missing_findings,
    render_vision_requirement_report,
)

# Провайдер -> имя переменной окружения с ключом, то же имя, что в
# nadzor-ai/.env.example (GIGACHAT_CREDENTIALS уже используется полным
# приложением, scripts/start-all.sh). Явный --api-key всегда в приоритете —
# переменная окружения только избавляет от необходимости передавать ключ
# аргументом командной строки (виден в истории shell/процессов).
_PROVIDER_ENV_KEY = {
    "gigachat": "GIGACHAT_CREDENTIALS",
    "yandexgpt": "YANDEX_GPT_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

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


def _load_text_facts(paths: list[str]) -> list[dict]:
    """[{page, text}] по ВСЕМ страницам всех файлов одной стороны — включая
    страницы, которые `extract_document_facts`/`material.py` исключил бы из
    сравнения как каталог поставщика.

    Намеренно НЕ `facts.text_facts` (см. `_registry` выше, для rooms/equipment
    он там правильный). Реальное измерение на этом же комплекте (Г.34):
    таблица подбора вентиляторов «Проект: <модель>», которая несёт код
    системы (ВД1, ПД22, ...) — единственный текстовый след кода в РД, —
    физически подшита ВНУТРИ 419-страничного коммерческого предложения
    поставщика (том РД-ОВ1, часть 2). material.py правильно исключает эти
    страницы из реестров помещений/оборудования (иначе туда прорвётся
    прайс-лист) — но узкий поиск присутствия короткого кода-токена
    («ВД1» как отдельное слово) на этих же страницах не несёт того риска
    шума, ради которого страница исключена: это не парсинг строки реестра,
    а да/нет по конкретной подстроке. Применить тот же фильтр здесь —
    значит потерять единственный источник подтверждения кода в РД (в
    прогоне на реальном комплекте без этого — 0 из 27 подтверждено вместо
    27 из 27, при том что сами коды физически присутствуют в файле)."""
    out: list[dict] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            print(f"пропущен (не найден): {path}", file=sys.stderr)
            continue
        try:
            doc = pymupdf.open(str(p))
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
            print(f"пропущен ({exc}): {path}", file=sys.stderr)
            continue
        try:
            for i in range(doc.page_count):
                text = doc[i].get_text("text").strip()
                if text:
                    out.append({"page": i + 1, "text": text})
        finally:
            doc.close()
    return out


def run_requirements(
    before_paths: list[str],
    after_paths: list[str],
    vision_config: Optional[LlmConfig] = None,
) -> None:
    """Сверка реестра требований из прозы ПД против корпуса РД (Г.32/Г.33,
    requirement_registry.py + requirement_cross_check.py) — не по
    key-реестру, как rooms/equipment, а по двум формам предложений
    прозы, поэтому отдельный путь, не через `run()`/`_registry`.

    `vision_config` — если задан, находки «predicate_missing_in_rd»
    (требование без кода, не найденное в тексте РД — по определению
    кандидат, не вердикт, см. requirement_cross_check.py) эскалируются в
    зрение по листу РД, где встречается хотя бы одно из помещений
    требования (vision_page_compare.py). Без ключа — только текстовый
    отчёт, как раньше."""
    before = [DocumentInput("ПД", 1, text_facts=_load_text_facts(before_paths))]
    after = [DocumentInput("РД", 1, text_facts=_load_text_facts(after_paths))]
    result = cross_check_requirements(before, after)
    print()
    print(render_requirement_cross_check_report(result))

    if vision_config is None:
        return
    room_index = _registry(after_paths, "room_facts")
    vision_results = check_predicate_missing_findings(result.findings, room_index, vision_config)
    print()
    print(render_vision_requirement_report(vision_results))


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
    parser.add_argument("--kind", choices=["rooms", "equipment", "requirements", "both"], default="both")
    parser.add_argument("--verify", action="store_true",
                        help="Прогнать кандидатов «только с одной стороны» через LLM (по одной картинке) — реальная позиция или шум извлечения")
    parser.add_argument("--verify-requirements", action="store_true",
                        help="Эскалировать требования без кода, не найденные в тексте РД (predicate_missing_in_rd), в зрение по листу РД — vision_page_compare.py")
    parser.add_argument("--provider", default="gigachat", choices=["openai", "anthropic", "google", "yandexgpt", "gigachat"])
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="",
                        help=f"По умолчанию берётся из переменной окружения по провайдеру ({', '.join(_PROVIDER_ENV_KEY.values())}), если не передан явно")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get(_PROVIDER_ENV_KEY.get(args.provider, ""), "")
    llm_config = LlmConfig(provider=args.provider, api_key=api_key, base_url=args.base_url, model=args.model)
    config = llm_config if args.verify else None
    vision_config = llm_config if args.verify_requirements else None

    if args.verify_requirements and not api_key:
        print("--verify-requirements задан, но ключ не найден (ни --api-key, ни переменная окружения) — эскалация в зрение пропущена", file=sys.stderr)
        vision_config = None

    if args.kind == "both":
        kinds = ["rooms", "equipment"]
    elif args.kind == "requirements":
        kinds = []
    else:
        kinds = [args.kind]
    for kind in kinds:
        run(args.before, args.after, kind, config)

    if args.kind in ("requirements", "both"):
        run_requirements(args.before, args.after, vision_config)


if __name__ == "__main__":
    main()
