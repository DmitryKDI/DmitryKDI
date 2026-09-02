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
from app.equip_cross_check import cross_check_equipment, render_equip_cross_check_report  # noqa: E402
from app.escalation import build_tickets, render_tickets_markdown  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.matching import DocumentInput, match_page_pairs  # noqa: E402
from app.router import classify_all_pairs  # noqa: E402
from app.requirement_cross_check import (  # noqa: E402
    cross_check_general_requirements,
    cross_check_requirements,
    render_general_requirement_cross_check_report,
    render_requirement_cross_check_report,
)
from app.level_pages import augment_room_index_with_level_fallback  # noqa: E402
from app.requirement_llm_extract import extract_requirements_llm  # noqa: E402
from app.requirement_text_verify import render_text_verify_report, verify_general_requirements_llm  # noqa: E402
from app.requirement_registry import (  # noqa: E402
    extract_general_requirements,
    extract_requirements,
    render_general_requirements_summary,
    render_requirements_summary,
)
from app.room_cross_check import cross_check_rooms, render_cross_check_report  # noqa: E402
from app.routing_diff import diff_room_routing, render_routing_diff_report  # noqa: E402
from app.set_overview import (  # noqa: E402
    compare_section_coverage,
    render_section_coverage_report,
    render_volume_summary,
    summarize_set,
)
from app.triangulation import (  # noqa: E402
    Signal,
    candidates_only,
    confirmed_only,
    signals_from_equip_cross_check,
    signals_from_requirement_cross_check,
    signals_from_room_cross_check,
    signals_from_routing_diff,
    triangulate,
)
from app.vision import (  # noqa: E402
    compare_page_pair,
    compare_text_pair,
    render_page_to_png_bytes,
    verify_candidate,
)
from app.ventilation_mo import (  # noqa: E402
    cross_check_mo_branches,
    extract_branch_locations,
    extract_mo_table_page,
    find_uncovered_rooms,
    is_mo_table_page,
    render_mo_cross_check_report,
)
from app.visual_prefilter import diff_hot_zone, is_visually_different  # noqa: E402
from app.vision_page_compare import (  # noqa: E402
    check_visual_candidates,
    render_vision_finding_line,
    render_vision_requirement_report,
)
from app.verdict_synthesis import (  # noqa: E402
    render_verdict_report,
    synthesize_all,
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

# Верхняя граница автовыбора помещений для routing_diff.py в run_triangulated
# (Г.50), когда --rooms не задан явно, но есть ключ ИИ: ~6 с/лист (Г.30) на
# кандидатную страницу — без потолка авто-прогон на комплекте с десятками
# отмеченных помещений растянется на много минут просто на геометрию.
MAX_AUTO_ROUTING_ROOMS = 15


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


def _document_inputs(paths: list[str]) -> tuple[list[DocumentInput], list[str]]:
    """PDF-пути -> `DocumentInput` с уже извлечёнными фактами — общая точка
    входа для сверок, которым нужен не один срез фактов (`_registry`,
    `_load_text_facts`), а полный набор сразу: `room_cross_check.py` и
    `equip_cross_check.py` (Г.9/Г.20 по всему комплекту, не по одной паре
    листов) ожидают на входе именно этот тип.

    Возвращает `(docs, skipped)` — пропущенные файлы раньше уходили только
    в stderr, а `--out`-файл (единственное, что реально читает
    пользователь после долгого прогона) их не видел вообще: полный отказ
    всех файлов стороны печатал чистый, обманчиво «зелёный» отчёт вместо
    честного «прогон недействителен» (независимый ревью, регрессия
    ровно того класса, что Г.10 запрещает)."""
    out: list[DocumentInput] = []
    skipped: list[str] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            reason = "не найден"
            skipped.append(f"{path}: {reason}")
            print(f"пропущен ({reason}): {path}", file=sys.stderr)
            continue
        try:
            facts = extract_document_facts(str(p), p.name)
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
            skipped.append(f"{path}: {exc}")
            print(f"пропущен ({exc}): {path}", file=sys.stderr)
            continue
        out.append(DocumentInput(
            name=p.name, pages=facts.pages, text_facts=facts.text_facts,
            room_facts=facts.room_facts, page_kinds=facts.page_kinds,
            equipment_facts=facts.equipment_facts, balance_facts=facts.balance_facts,
        ))
    return out, skipped


def run_page_pair_comparison(
    before_docs: list[DocumentInput],
    before_paths: list[str],
    after_docs: list[DocumentInput],
    after_paths: list[str],
    config: LlmConfig,
    on_result=None,
) -> list[dict]:
    """Прямое сравнение листов ПД↔РД по паре — чертёж с чертежом, текст с
    текстом, без требования из ПД как повода (Г.51). Пользователь прямо
    указал: чертежи сравниваются между собой напрямую, а не только через
    то, что о них сказано текстом.

    Это не новый механизм — тот же самый, что уже работает в живом API
    (`main.py._run_analysis`, единственный путь, где он раньше вызывался):
    `match_page_pairs` (Г.4-Г.6) находит пары листов по смыслу наименования
    и штампу; `router.classify_pair` (Г.23/25/28) — трёхуровневая схема,
    построенная и покрытая тестами ещё в той сессии, но, как и остальные
    модули Г.46, ни разу не вызывавшаяся ни из `main.py`, ни отсюда —
    решает, каким парам вообще нужен ИИ: реестры помещений/оборудования
    страницы уже совпали → уровень 0, пропуск без вызова; расхождение в
    реестрах → уровень 2, ИИ обязателен; текстовая пара с diff выше
    порога → уровень 3, ИИ условно. Дальше `compare_page_pair`
    (чертёж/чертёж, зрением) или `compare_text_pair` (текст/текст) —
    ровно то же, что уже проверено вживую в главном приложении, здесь без
    привязки к БД.

    Г.54: level=0 не означает безусловный пропуск — реестры могут совпасть
    текстом, а сам чертёж отличаться (конфигурация воздуховодов и т.п.,
    которую ни room_facts, ни equipment_facts не видят в принципе). Для
    пар-чертежей уровня 0 дешёвый пиксельный предфильтр (`visual_prefilter`,
    без ИИ) сравнивает сами рендеры — заметно другие промоутируются в
    очередь на зрение (`promoted_by_visual_diff=True` в записи результата).

    Возвращает список записей `{before_path, before_page, after_path,
    after_page, page_kind, level, promoted_by_visual_diff, status, error,
    findings, injection_suspected}` — по одной на каждую пару уровня 2/3 И
    на каждую промоутированную пару уровня 0, включая сорванные вызовы
    (Г.10 — не молчаливый пропуск, `status="error"` с причиной)."""
    pairs = match_page_pairs(before_docs, after_docs)
    verdicts = classify_all_pairs(pairs, before_docs, after_docs, [], [])

    out: list[dict] = []
    for v in verdicts:
        p = v.pair
        before_path = before_paths[p.before_file_idx]
        after_path = after_paths[p.after_file_idx]
        promoted_by_visual_diff = False
        if v.level == 0:
            # Г.54: реестры помещений/оборудования совпали — но конфигурация
            # НА САМОМ ЧЕРТЕЖЕ (обвязка воздуховодов и т.п.) реестрами не
            # видна вообще, ни как совпадение, ни как расхождение (реальный
            # пропущенный случай слепого прогона). Дешёвый пиксельный
            # предфильтр без ИИ — не пропускать пару молча, если рендеры
            # заметно разные, несмотря на «совпавший» текстовый реестр.
            if p.page_kind == "drawing" and is_visually_different(
                before_path, p.before_page, after_path, p.after_page
            ):
                promoted_by_visual_diff = True
            if not promoted_by_visual_diff:
                continue
        before_doc = before_docs[p.before_file_idx]
        after_doc = after_docs[p.after_file_idx]
        context = f"раздел {before_doc.discipline_code or '?'}"
        if p.matched_by == "position" and p.discipline_mismatch:
            context += " (сопоставлено по позиции, разделы штампа не совпадают — проверьте применимость)"
        if promoted_by_visual_diff:
            context += " (Г.54: реестры совпали, но рендеры листов визуально отличаются — пиксельный предфильтр)"

        crop_zone = None
        try:
            if p.page_kind == "text":
                before_text = "\n".join(f["text"] for f in before_doc.text_facts if f["page"] == p.before_page)
                after_text = "\n".join(f["text"] for f in after_doc.text_facts if f["page"] == p.after_page)
                result = compare_text_pair(before_text, after_text, config,
                                           context=context, discipline=before_doc.discipline_code)
            else:
                # Г.55: на насыщенном листе (десятки-сотни помещений) одно
                # локальное изменение тонет при сравнении целиком —
                # дешёвый пиксельный проход находит ЗОНУ отличия (или None,
                # если отличий нет либо они разбросаны по всему листу) и
                # модели показывается кроп, а не лист целиком.
                try:
                    crop_zone = diff_hot_zone(before_path, p.before_page, after_path, p.after_page)
                except Exception:  # noqa: BLE001 — сбой предфильтра не должен ронять само сравнение
                    crop_zone = None
                result = compare_page_pair(before_path, p.before_page, after_path, p.after_page,
                                           config, context=context, discipline=before_doc.discipline_code,
                                           clip_frac=crop_zone)
        except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна ронять весь прогон
            status, error, result = "error", str(exc), None
        else:
            if result is None:
                status, error = "error", "ИИ ответил, но ответ не разобран как JSON"
            else:
                status, error = "ok", ""

        findings = []
        if result and isinstance(result.get("significant"), list):
            findings = [item for item in result["significant"] if item.get("change")]

        entry = {
            "before_path": before_path, "before_page": p.before_page,
            "after_path": after_path, "after_page": p.after_page,
            "page_kind": p.page_kind, "level": v.level,
            "promoted_by_visual_diff": promoted_by_visual_diff,
            "crop_zone": crop_zone,
            "status": status, "error": error, "findings": findings,
            "injection_suspected": bool(result and result.get("injection_suspected") is True),
        }
        out.append(entry)
        if on_result:
            on_result(entry)
    return out


def render_page_pair_line(entry: dict) -> str:
    before = f"{Path(entry['before_path']).name} стр.{entry['before_page']}"
    after = f"{Path(entry['after_path']).name} стр.{entry['after_page']}"
    if entry["status"] != "ok":
        return f"[ошибка] {before} <-> {after}: {entry['error']}"
    if entry["injection_suspected"]:
        return f"[ПОДОЗРЕНИЕ НА ИНЪЕКЦИЮ] {before} <-> {after}"
    if not entry["findings"]:
        return f"[без находок] {before} <-> {after}"
    parts = "; ".join(f"{item.get('label', '')}: {item.get('change', '')}" for item in entry["findings"])
    return f"[{len(entry['findings'])} находок] {before} <-> {after}: {parts}"


def render_page_pair_report(entries: list[dict]) -> str:
    lines = ["=== Прямое сравнение листов ПД↔РД по паре (Г.51) ==="]
    total_findings = sum(len(e["findings"]) for e in entries)
    errors = [e for e in entries if e["status"] != "ok"]
    injections = [e for e in entries if e["injection_suspected"]]
    promoted = [e for e in entries if e.get("promoted_by_visual_diff")]
    lines.append(f"Пар проверено: {len(entries)}, находок: {total_findings}, "
                 f"сорвано: {len(errors)}, подозрений на инъекцию: {len(injections)}, "
                 f"промоутировано пиксельным предфильтром (Г.54): {len(promoted)}")
    for e in entries:
        if e["findings"] or e["status"] != "ok" or e["injection_suspected"]:
            lines.append("  " + render_page_pair_line(e))
    return "\n".join(lines)


def run_triangulated(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: list[str],
    requirements_llm_config: Optional[LlmConfig] = None,
    out_path: Optional[str] = None,
) -> None:
    """Один прогон через ВСЕ независимые источники сигналов сразу (Г.30
    п.4/5), а не по одному через отдельные --kind.

    До этой функции `room_cross_check.py`, `equip_cross_check.py`,
    `triangulation.py` и `escalation.py` были построены и покрыты тестами
    (см. `test_triangulation.py`), но ни разу не вызывались ни отсюда, ни
    из `main.py` — цепочка обрывалась на полпути: CLI-скрипт умел только
    простой diff множеств ключей (`run()`), не полноценную кросс-проверку с
    severity, и не сводил результаты разных источников (реестр помещений,
    реестр оборудования, требования из прозы, граф маршрутизации) в единый
    вердикт. Слишком много независимых модулей ("переменных") при ручном
    выборе --kind означало, что реальный прогон трогал только подмножество
    из них за раз, а не всю цепочку.

    Каждый источник даёт `Signal` по своему домену/ключу (см.
    `triangulation.signals_from_*`); `triangulate()` помечает находку
    `confirmed`, только если её независимо подтвердили ≥2 разных источника
    — иначе `candidate`, и тогда `escalation.build_tickets()` формирует не
    молчаливое «не найдено», а конкретный пакет: какие источники уже
    согласны, каких не хватает, что проверить, чтобы закрыть вопрос.

    Граф маршрутизации (`routing_diff.py`) участвует, только если задан
    `room_keys` — он не сканирует комплект целиком (~6 с/лист, Г.30),
    поэтому без явного списка помещений просто пропускается, с видимым
    объяснением (Г.10), а не тихо."""
    out_f = open(out_path, "a", encoding="utf-8") if out_path else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        before_docs, before_skipped = _document_inputs(before_paths)
        after_docs, after_skipped = _document_inputs(after_paths)
        skipped = before_skipped + after_skipped
        if skipped:
            _emit(f"!!! ПРОПУЩЕНО ФАЙЛОВ: {len(skipped)} (не открылись или не найдены) !!!")
            for line in skipped:
                _emit(f"  {line}")
        if not before_docs or not after_docs:
            _emit(
                f"\n!!! ПРОГОН НЕДЕЙСТВИТЕЛЕН: сторона {'ПД' if not before_docs else 'РД'} "
                f"пуста (ПД {len(before_docs)}/{len(before_paths)}, РД {len(after_docs)}/{len(after_paths)}) "
                f"— ни один вывод ниже не был бы основан на реальных данных, прогон остановлен !!!"
            )
            return

        room_result = cross_check_rooms(before_docs, after_docs)
        _emit(render_cross_check_report(room_result))
        equip_result = cross_check_equipment(before_docs, after_docs)
        _emit(render_equip_cross_check_report(equip_result))

        pd_text_facts = _load_text_facts(before_paths)
        if requirements_llm_config is not None:
            pd_requirements = extract_requirements_llm(pd_text_facts, requirements_llm_config)
        else:
            pd_requirements = extract_requirements(pd_text_facts)
        _emit(render_requirements_summary(pd_requirements))
        _emit("")
        general_requirements = extract_general_requirements(pd_text_facts)
        _emit(render_general_requirements_summary(general_requirements))
        req_after = [DocumentInput("РД", 1, text_facts=_load_text_facts(after_paths))]
        req_result = cross_check_requirements(pd_requirements, req_after)
        _emit(render_requirement_cross_check_report(req_result))
        _emit("")
        general_req_result = cross_check_general_requirements(general_requirements, req_after)
        _emit(render_general_requirement_cross_check_report(general_req_result))

        if requirements_llm_config is not None:
            confirmed_sentences = {f.sentence_pd for f in general_req_result.findings
                                    if f.finding_type == "token_confirmed_in_rd"}
            pending_general = [r for r in general_requirements if r.sentence not in confirmed_sentences]
            _emit("")
            _emit("=== Семантическая сверка общих требований с текстом РД (эскалация Г.49) — по мере готовности ===")
            text_verify_results = verify_general_requirements_llm(
                pending_general, req_after[0].text_facts, requirements_llm_config,
                on_result=lambda r: _emit(f"[{r['verdict']}] стр.{r['page']}: {r['reason']}"),
            )
            _emit("")
            _emit(render_text_verify_report(text_verify_results))

        signals: list[Signal] = []
        signals += signals_from_room_cross_check(room_result.findings)
        signals += signals_from_equip_cross_check(equip_result.findings)
        signals += signals_from_requirement_cross_check(req_result.findings)

        routing_room_keys = room_keys
        auto_selected = False
        if not routing_room_keys and requirements_llm_config is not None:
            # Комплексный прогон (реальный ключ, Г.50): без --rooms граф
            # маршрутизации раньше просто пропускался — а именно в таком
            # прогоне пользователь ожидает проверку "и по тексту, и по
            # чертежу" разом (~6 с/лист, Г.30, поэтому без ключа остаётся
            # опциональным через явный --rooms, а не включается всегда).
            # Кандидаты — только помещения, УЖЕ отмеченные расхождением в
            # реестре (Г.9/room_cross_check) — точечно, не весь комплект.
            candidates = sorted({f.room_key for f in room_result.findings})
            routing_room_keys = candidates[:MAX_AUTO_ROUTING_ROOMS]
            auto_selected = True

        if routing_room_keys:
            if auto_selected:
                _emit(f"\n(--rooms не задан — граф маршрутизации автоматически проверен для "
                      f"{len(routing_room_keys)} из {len(candidates)} помещений с расхождением "
                      f"в реестре Г.9: {', '.join(routing_room_keys)})")
            routing_diff = diff_room_routing(before_paths, after_paths, routing_room_keys)
            _emit(render_routing_diff_report(routing_diff))
            signals += signals_from_routing_diff(routing_diff)
        else:
            _emit("\n(граф маршрутизации не проверялся — не задан --rooms, и автовыбор "
                  "недоступен без ключа ИИ; остальные источники не видят расхождений "
                  "чистой геометрии, см. routing_diff.py)")

        if requirements_llm_config is not None:
            _emit("")
            _emit("=== Прямое сравнение листов ПД↔РД по паре (Г.51) — по мере готовности ===")
            pair_results = run_page_pair_comparison(
                before_docs, before_paths, after_docs, after_paths, requirements_llm_config,
                on_result=lambda r: _emit(render_page_pair_line(r)),
            )
            _emit("")
            _emit(render_page_pair_report(pair_results))

        if requirements_llm_config is not None:
            room_index = _registry(after_paths, "room_facts")
            room_index = augment_room_index_with_level_fallback(room_index, after_paths)
            _emit("")
            _emit("=== Проверка кандидатов зрением по листу РД (эскалация Г.33) — по мере готовности ===")
            vision_results = check_visual_candidates(
                req_result.findings, room_index, requirements_llm_config,
                on_result=lambda r: _emit(render_vision_finding_line(r)),
            )
            for r in vision_results:
                if r["verdict"] == "absent":
                    for room in r["rooms"]:
                        signals.append(Signal(source="vision", domain="room", key=room, detail=r["reason"]))

        confirmations = triangulate(signals)
        confirmed = confirmed_only(confirmations)
        candidates = candidates_only(confirmations)
        _emit("")
        _emit(f"=== Триангуляция источников (Г.30 п.4): сигналов {len(signals)}, ключей {len(confirmations)} ===")
        _emit(f"Подтверждено ≥2 источниками: {len(confirmed)}")
        for c in confirmed:
            _emit(f"  [{c.domain}] {c.key}: {', '.join(c.sources)}")

        tickets = build_tickets(candidates)
        _emit("")
        _emit(render_tickets_markdown(tickets))

        if requirements_llm_config is not None and signals:
            _emit("")
            _emit("=== Сводный вердикт по источникам (Г.61) — по мере готовности ===")
            verdicts = synthesize_all(
                signals, requirements_llm_config,
                on_result=lambda v: _emit(f"  [{v.verdict}] {v.domain} {v.key} "
                                           f"({', '.join(v.sources)}): {v.reasoning}"),
            )
            _emit("")
            _emit(render_verdict_report(verdicts))
    finally:
        if out_f:
            out_f.close()


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
        _emit("")
        general_requirements = extract_general_requirements(pd_text_facts)
        _emit(render_general_requirements_summary(general_requirements))

        after = [DocumentInput("РД", 1, text_facts=_load_text_facts(after_paths))]
        result = cross_check_requirements(pd_requirements, after)
        _emit("")
        _emit(render_requirement_cross_check_report(result))
        _emit("")
        general_req_result = cross_check_general_requirements(general_requirements, after)
        _emit(render_general_requirement_cross_check_report(general_req_result))

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

        confirmed_sentences = {f.sentence_pd for f in general_req_result.findings
                                if f.finding_type == "token_confirmed_in_rd"}
        pending_general = [r for r in general_requirements if r.sentence not in confirmed_sentences]
        _emit("")
        _emit("=== Семантическая сверка общих требований с текстом РД (эскалация Г.49) — по мере готовности ===")
        text_verify_results = verify_general_requirements_llm(
            pending_general, after[0].text_facts, llm_config,
            on_result=lambda r: _emit(f"[{r['verdict']}] стр.{r['page']}: {r['reason']}"),
        )
        _emit("")
        _emit(render_text_verify_report(text_verify_results))
    finally:
        if out_f:
            out_f.close()


def run_mo_check(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: list[str],
    llm_config: LlmConfig,
    out_path: Optional[str] = None,
) -> None:
    """Сверка местных отсосов (вытяжных шкафов) ПД↔РД по «Таблице
    воздухообменов помещений» (Г.58) — механизм, найденный вручную для
    нарушения №3 конкретного объекта: таблица ПД задаёт по помещению
    систему и ветки М.О., план РД показывает те же ветки нарисованными —
    иногда у ДРУГОГО помещения или под ДРУГИМ обозначением системы.

    Требует ключ ИИ и явный список `room_keys` (как `--kind routing`, не
    как `--kind all` без ключа) — оба листа читаются только зрением, узкая
    и дорогая операция, не однажды на весь комплект."""
    out_f = open(out_path, "a", encoding="utf-8") if out_path else None

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        pd_entries: list[dict] = []
        rooms_seen_all: set[str] = set()
        table_pages_found = 0
        for path in before_paths:
            text_facts = _load_text_facts([path])
            pages = sorted({f["page"] for f in text_facts})
            for page in pages:
                if not is_mo_table_page(text_facts, page):
                    continue
                table_pages_found += 1
                page_result = extract_mo_table_page(path, page, llm_config)
                pd_entries.extend(page_result["rooms"])
                rooms_seen_all.update(page_result["rooms_seen"])
        if room_keys:
            pd_entries = [r for r in pd_entries if r.get("room") in room_keys]
        _emit(f"=== Сверка местных отсосов ПД↔РД (Г.58) ===")
        _emit(f"Листов «Таблица воздухообменов» найдено: {table_pages_found}, "
              f"помещений с местными отсосами: {len(pd_entries)}")
        if room_keys:
            uncovered = find_uncovered_rooms(room_keys, rooms_seen_all)
            if uncovered:
                _emit(f"Запрошенные помещения не найдены в таблице воздухообменов "
                      f"ВООБЩЕ (не строкой, а не пустым столбцом М.О. — Г.60, нужен "
                      f"другой источник ПД): {', '.join(uncovered)}")
        if not pd_entries:
            _emit("Ни одного помещения с местными отсосами не найдено "
                  "(нет таблицы воздухообменов в ПД, либо у запрошенных "
                  "помещений её в принципе нет — не путать с молчанием, Г.10)")
            return

        room_index = _registry(after_paths, "room_facts")
        candidate_pages: set[tuple[str, int]] = set()
        for entry in pd_entries:
            for ref in room_index.get(entry.get("room", ""), []):
                candidate_pages.add((ref["path"], ref["page"]))

        rd_branches: list[dict] = []
        for path, page in sorted(candidate_pages):
            rd_branches.extend(extract_branch_locations(path, page, llm_config))
        _emit(f"Листов РД просмотрено: {len(candidate_pages)}, веток найдено: {len(rd_branches)}")

        findings = cross_check_mo_branches(pd_entries, rd_branches)
        _emit("")
        _emit(render_mo_cross_check_report(findings))
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
    parser.add_argument("--kind", choices=["rooms", "equipment", "requirements", "routing", "mo", "both", "all"], default="all",
                        help="'all' (по умолчанию) — единая цепочка через ВСЕ источники сразу: реестры помещений/"
                             "оборудования (кросс-проверка с severity, не только diff множеств), требования из прозы "
                             "ПД, граф маршрутизации (если задан --rooms) — со сведением в триангуляцию (Г.30 п.4) и "
                             "очередью эскалации по кандидатам с одним источником. Остальные значения — узкие "
                             "прогоны одного источника, для отладки/точечной проверки.")
    parser.add_argument("--rooms", default="",
                        help="Список номеров помещений через запятую — прицельная сверка графа маршрутизации "
                             "ПД↔РД (routing_diff.py, Г.30, без LLM). Обязателен для --kind routing; для --kind all "
                             "без него граф маршрутизации просто пропускается (см. вывод).")
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

    if args.kind == "all":
        room_keys = [r.strip() for r in args.rooms.split(",") if r.strip()]
        run_triangulated(args.before, args.after, room_keys, requirements_llm_config, out_path=args.out or None)
        return

    if args.kind == "both":
        kinds = ["rooms", "equipment"]
    elif args.kind in ("requirements", "routing"):
        kinds = []
    else:
        kinds = [args.kind]
    for kind in kinds:
        run(args.before, args.after, kind, config)

    if args.kind == "routing":
        room_keys = [r.strip() for r in args.rooms.split(",") if r.strip()]
        if not room_keys:
            print("--kind routing требует --rooms с непустым списком номеров помещений через запятую", file=sys.stderr)
            return
        diff = diff_room_routing(args.before, args.after, room_keys)
        print()
        print(render_routing_diff_report(diff))

    if args.kind == "mo":
        room_keys = [r.strip() for r in args.rooms.split(",") if r.strip()]
        if not api_key:
            print("--kind mo требует ключ ИИ (оба листа читаются только зрением, Г.58)", file=sys.stderr)
            return
        run_mo_check(args.before, args.after, room_keys, provider_config, out_path=args.out or None)

    if args.kind in ("requirements", "both"):
        run_requirements(args.before, args.after, requirements_llm_config, out_path=args.out or None)


if __name__ == "__main__":
    main()
