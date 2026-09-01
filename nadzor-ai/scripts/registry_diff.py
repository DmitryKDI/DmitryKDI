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
from app.level_pages import augment_room_index_with_level_fallback  # noqa: E402
from app.requirement_llm_extract import extract_requirements_llm  # noqa: E402
from app.requirement_registry import extract_requirements, render_requirements_summary  # noqa: E402
from app.set_overview import (  # noqa: E402
    compare_section_coverage,
    render_section_coverage_report,
    render_volume_summary,
    summarize_set,
)
from app.vision import render_page_to_png_bytes, verify_candidate  # noqa: E402
from app.vision_page_compare import (  # noqa: E402
    check_visual_candidates,
    render_vision_finding_line,
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


def run_overview(before_paths: list[str], after_paths: list[str], out_path: Optional[str] = None) -> None:
    """Обзор комплекта (см. `set_overview.py`): краткая сводка по каждому
    тому и сравнение разделов ПД↔РД по составу — печатается ПЕРВЫМ делом,
    до любого --kind, дешёво и без LLM (только классификация раздела +
    уже существующие текстовые экстракторы). Пользовательский запрос:
    разбор не должен ощущаться «зашитым под один раздел» — этот обзор
    первым показывает, что вообще пришло с обеих сторон, ДО погружения в
    сравнение содержимого одного раздела.

    Открывает `out_path` в режиме "w" (создаёт файл заново) — это первая
    запись во всём прогоне `main()`; `run_requirements`, если тоже вызван
    следом с тем же путём, дописывает в этот же файл (режим "a"), не
    затирает обзор."""
    out_f = open(out_path, "w", encoding="utf-8") if out_path else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        before = summarize_set(before_paths)
        after = summarize_set(after_paths)
        _emit(render_volume_summary(before, "ПД"))
        _emit(render_volume_summary(after, "РД/ИД"))
        _emit(render_section_coverage_report(compare_section_coverage(before, after)))
    finally:
        if out_f:
            out_f.close()


def run_requirements(
    before_paths: list[str],
    after_paths: list[str],
    llm_config: Optional[LlmConfig] = None,
    out_path: Optional[str] = None,
) -> None:
    """Сверка реестра требований из прозы ПД против корпуса РД
    (Г.32/Г.33/Г.36) — не по key-реестру, как rooms/equipment, а по
    произвольной прозе, поэтому отдельный путь, не через `run()`/`_registry`.

    Извлечение требований из ПД — ДВА взаимозаменяемых пути, сверка
    (`cross_check_requirements`) от источника не зависит:

      - `llm_config` задан: `requirement_llm_extract.py` — общий путь,
        работает на прозе любого формата и раздела, требует ключ ЛЛМ.
      - `llm_config` не задан: `requirement_registry.py` — узкий regex-
        путь по одному наблюдённому формату списка, без ключа ЛЛМ
        (RUN-NO-LLM.bat); см. предупреждение в докстринге самого модуля.

    Тот же `llm_config`, если задан, используется и для эскалации находок
    «no_code_visual_check_needed» в зрение по листу РД
    (`vision_page_compare.py`) — тот же ключ и провайдер нужны для обоих
    шагов, отдельного флага/ключа под извлечение не заводится.

    `out_path`, если задан, дублирует весь вывод в файл — не только в
    конце, а по мере готовности каждой части (сводка требований сразу
    после извлечения, вердикт зрения сразу после каждой находки, а не
    списком после последнего вызова модели). Причина: извлечение и особенно
    vision-эскалация занимают минуты на десятках находок, и если прогон
    прервётся или ведущий его агент потеряет промежуточный вывод из виду —
    то, что уже посчитано, должно остаться читаемым на диске, а не только
    в уже прокрученном выводе терминала. Файл же годится и для ручного
    поиска — сводка требований по всем страницам ПД в одном месте, без
    повторного запуска конвейера. Файл открывается на ДОЗАПИСЬ ("a") — если
    в этом же прогоне уже писал `run_overview` с тем же путём, обзор
    комплекта остаётся в начале файла, а не затирается."""
    out_f = open(out_path, "a", encoding="utf-8") if out_path else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        pd_text_facts = _load_text_facts(before_paths)
        if llm_config is not None:
            pd_requirements = extract_requirements_llm(pd_text_facts, llm_config)
        else:
            pd_requirements = extract_requirements(pd_text_facts)
        _emit(render_requirements_summary(pd_requirements))

        after = [DocumentInput("РД", 1, text_facts=_load_text_facts(after_paths))]
        result = cross_check_requirements(pd_requirements, after)
        _emit("")
        _emit(render_requirement_cross_check_report(result))

        if llm_config is None:
            return
        room_index = _registry(after_paths, "room_facts")
        room_index = augment_room_index_with_level_fallback(room_index, after_paths)
        _emit("")
        _emit("=== Проверка кандидатов зрением по листу РД (эскалация Г.33) — по мере готовности ===")
        vision_results = check_visual_candidates(
            result.findings, room_index, llm_config,
            on_result=lambda r: _emit(render_vision_finding_line(r)),
        )
        _emit("")
        _emit(render_vision_requirement_report(vision_results))
    finally:
        if out_f:
            out_f.close()


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
                        help="Требования из ПД извлекать ЛЛМ (общий путь, requirement_llm_extract.py — иначе узкий regex-путь без ключа) "
                             "и эскалировать кандидатов без кода (no_code_visual_check_needed) в зрение по листу РД — vision_page_compare.py")
    parser.add_argument("--provider", default="gigachat", choices=["openai", "anthropic", "google", "yandexgpt", "gigachat"])
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="",
                        help=f"По умолчанию берётся из переменной окружения по провайдеру ({', '.join(_PROVIDER_ENV_KEY.values())}), если не передан явно")
    parser.add_argument("--out", default="",
                        help="Путь к файлу: обзор комплекта и (для --kind requirements) сводка требований ПД и вердикты "
                             "зрения пишутся туда по мере готовности (не только в конце), не только в stdout")
    parser.add_argument("--no-overview", action="store_true",
                        help="Не печатать обзор комплекта (сводка по каждому тому + сравнение разделов ПД↔РД) в начале")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get(_PROVIDER_ENV_KEY.get(args.provider, ""), "")
    provider_config = LlmConfig(provider=args.provider, api_key=api_key, base_url=args.base_url, model=args.model)
    config = provider_config if args.verify else None
    requirements_llm_config = provider_config if args.verify_requirements else None

    if args.verify_requirements and not api_key:
        print("--verify-requirements задан, но ключ не найден (ни --api-key, ни переменная окружения) — извлечение и эскалация через ЛЛМ пропущены, используется regex-путь без зрения", file=sys.stderr)
        requirements_llm_config = None

    if not args.no_overview:
        run_overview(args.before, args.after, out_path=args.out or None)

    if args.kind == "both":
        kinds = ["rooms", "equipment"]
    elif args.kind == "requirements":
        kinds = []
    else:
        kinds = [args.kind]
    for kind in kinds:
        run(args.before, args.after, kind, config)

    if args.kind in ("requirements", "both"):
        run_requirements(args.before, args.after, requirements_llm_config, out_path=args.out or None)


if __name__ == "__main__":
    main()
