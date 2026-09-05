"""Реальный движок Приложения Г («сравнение комплектов») как вызываемая
функция для FastAPI — не только для `scripts/registry_diff.py`.

`registry_diff.run_triangulated()` (Г.46/Г.61/Г.64/Г.65) — единственное
место, где реестры помещений/оборудования, требования из прозы,
комплектность (Г.17) и (при наличии ключа ИИ) граф маршрутизации сводятся
в триангуляцию (`triangulation.py`) и очередь эскалации (`escalation.py`).
Но это скрипт: он печатает текст на экран/в файл (`_emit`), а не
возвращает структуру. HTTP-эндпоинту (`main.py`) нужен JSON, поэтому здесь
— тот же порядок вызовов, что и в `run_triangulated`, но результат
собирается в словари вместо печати. Это НЕ новая логика: каждая функция,
вызванная ниже, — импорт из уже протестированного `app/*`, тот же путь,
что доказан на реальном комплекте (см. `nadzor-ai/CLAUDE.md`, Приложение
Г, `docs/PRILOZHENIE-G-ISTORIYA.md`).

Режим без ключа ИИ (`llm_config is None`) — тот же путь, что
`scripts/run_no_llm.sh`/`RUN-NO-LLM.bat` (Г.46): реестры помещений/
оборудования (с severity, не просто diff множеств), комплектность,
требования из прозы через узкий regex-путь (`requirement_registry.py`),
сверка требований с РД текстом, триангуляция и очередь эскалации. Это
честный, полностью рабочий деградированный режим, а не заглушка — граф
маршрутизации (если явно передан `room_keys`), таблица местных отсосов и
эскалация в зрение/сводный вердикт при этом отсутствуют, и это ВИДИМО в
ответе (`llm.used=False`, поле `not_run` перечисляет, что именно
пропущено и почему, Г.10), а не молчаливо.

Сознательно НЕ перенесено в этот эндпоинт (см. отчёт агента, Г.10 —
честно, не молчанием): прямое сравнение листов по паре зрением (Г.51,
`run_page_pair_comparison`) и таблица местных отсосов (Г.58/Г.65,
`_run_mo_cross_check`) — обе требуют ключа ИИ и по конструкции не
участвуют в самой триангуляции (их находки — либо отдельная секция
отчёта, либо сигнал `mo_table`, который не был точечно перепроверен в
этом эндпоинте на реальном комплекте). Следующий шаг, если понадобится:
подключить `mo_table`-сигналы и вернуть секцию `page_pair_comparison`
отдельным полем, тем же способом, что уже сделано для `routing`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Optional

import pymupdf

from .composition_registry import (
    SuppliedDocument,
    check_completeness,
    extract_composition_entries,
    find_document_references,
)
from .escalation import build_tickets
from .llm import LlmConfig
from .matching import DocumentInput
from .equip_cross_check import cross_check_equipment
from .requirement_cross_check import cross_check_general_requirements, cross_check_requirements
from .requirement_llm_extract import extract_requirements_llm
from .requirement_registry import extract_general_requirements, extract_requirements
from .room_cross_check import cross_check_rooms
from .routing_diff import diff_room_routing
from .stamp import read_stamp
from .documents import extract_document_facts
from .triangulation import (
    Signal,
    candidates_only,
    confirmed_only,
    signals_from_equip_cross_check,
    signals_from_requirement_cross_check,
    signals_from_room_cross_check,
    signals_from_routing_diff,
    triangulate,
)
from .verdict_synthesis import KeyVerdict, synthesize_all

# Тот же потолок, что и в scripts/registry_diff.py (Г.50) — авто-выбор
# помещений для routing_diff, когда --rooms/room_keys не задан явно, но
# ключ ИИ есть: ~6 с/лист, прогон на десятках помещений без потолка растянется
# на много минут.
MAX_AUTO_ROUTING_ROOMS = 15


def _to_jsonable(value):
    """Датаклассы этого пакета -> обычные dict/list/str для FastAPI, без
    ручного перечисления полей на каждый тип (их тут больше десятка)."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


@dataclass
class DocumentLoadResult:
    docs: list[DocumentInput] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _load_documents(paths: list[str], names: Optional[list[str]] = None) -> DocumentLoadResult:
    """PDF-пути -> `DocumentInput` с уже извлечёнными фактами. Копия
    `registry_diff._document_inputs` (см. модульный докстринг: скрипты не
    предназначены для импорта, поэтому логика продублирована здесь как
    отдельная, тестируемая функция app-пакета, а не импортирована из
    scripts/) — файл, который не открылся, не роняет весь прогон, а
    попадает в `skipped` (Г.10 — видимое, не молчаливое).

    `names`, если задан, — отображаемое имя документа (оригинальное имя
    файла на момент загрузки через `/documents`, см. `main.py`), а не имя
    файла НА ДИСКЕ: `upload_document` сохраняет каждый файл под случайным
    UUID-именем (`{uuid4().hex}.pdf`), и без этого параметра инспектор
    увидел бы в ответе бессмысленный набор латинских символов вместо
    названия тома."""
    out: list[DocumentInput] = []
    skipped: list[str] = []
    for i, path in enumerate(paths):
        p = Path(path)
        display_name = names[i] if names else p.name
        if not p.is_file():
            skipped.append(f"{display_name}: не найден")
            continue
        try:
            facts = extract_document_facts(str(p), display_name)
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
            skipped.append(f"{display_name}: {exc}")
            continue
        out.append(DocumentInput(
            name=display_name, pages=facts.pages, text_facts=facts.text_facts,
            room_facts=facts.room_facts, page_kinds=facts.page_kinds,
            equipment_facts=facts.equipment_facts, balance_facts=facts.balance_facts,
        ))
    return DocumentLoadResult(docs=out, skipped=skipped)


def _load_text_facts(paths: list[str]) -> list[dict]:
    """[{page, text}] по ВСЕМ страницам — копия `registry_diff._load_text_facts`
    (Г.34: нужен нефильтрованный текст для комплектности/поиска токена
    требования, в отличие от `text_facts`, которые `material.py` уже
    отфильтровал от каталогов поставщика)."""
    out: list[dict] = []
    for path in paths:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            doc = pymupdf.open(str(p))
        except Exception:  # noqa: BLE001 — один битый файл не должен ронять весь прогон
            continue
        try:
            for i in range(doc.page_count):
                text = doc[i].get_text("text").strip()
                if text:
                    out.append({"page": i + 1, "text": text})
        finally:
            doc.close()
    return out


def _supplied_documents(paths: list[str], names: Optional[list[str]] = None) -> list[SuppliedDocument]:
    """Копия `registry_diff._supplied_documents` (Г.17) — обозначение из
    имени файла плюс хвост шифра из штампа первой страницы.

    `names` — то же, что и в `_load_documents`: реальное имя файла на
    момент загрузки, не случайное UUID-имя на диске (`composition_registry`
    парсит смысловые части ИМЕНИ ФАЙЛА, Г.17 — на UUID это совпадение
    никогда не сработает, находки комплектности молча превратятся в
    пустой список)."""
    out: list[SuppliedDocument] = []
    for i, path in enumerate(paths):
        display_name = names[i] if names else Path(path).name
        shifrs: tuple[str, ...] = ()
        try:
            doc = pymupdf.open(path)
            try:
                if doc.page_count:
                    stamp = read_stamp(doc[0])
                    if stamp.shifr:
                        shifrs = (stamp.shifr,)
            finally:
                doc.close()
        except Exception:  # noqa: BLE001 — не открылся файл; обозначение всё равно берём из имени
            pass
        out.append(SuppliedDocument(filename=display_name, shifrs=shifrs))
    return out


def run_triangulated_analysis(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: Optional[list[str]] = None,
    llm_config: Optional[LlmConfig] = None,
    before_names: Optional[list[str]] = None,
    after_names: Optional[list[str]] = None,
) -> dict:
    """Тот же порядок источников, что `registry_diff.run_triangulated`
    (Г.46), но результат — JSON-совместимый словарь, а не печать. См.
    докстринг модуля про то, что сознательно не перенесено (Г.51/mo).

    `before_names`/`after_names` — отображаемые имена документов (см.
    `_load_documents`), по умолчанию берутся из самих путей."""
    room_keys = list(room_keys or [])
    not_run: list[str] = []

    before = _load_documents(before_paths, before_names)
    after = _load_documents(after_paths, after_names)
    skipped = before.skipped + after.skipped

    if not before.docs or not after.docs:
        return {
            "valid": False,
            "reason": (
                f"прогон недействителен: сторона "
                f"{'ПД' if not before.docs else 'РД'} пуста "
                f"(ПД {len(before.docs)}/{len(before_paths)}, "
                f"РД {len(after.docs)}/{len(after_paths)})"
            ),
            "skipped_files": skipped,
        }

    room_result = cross_check_rooms(before.docs, after.docs)
    equip_result = cross_check_equipment(before.docs, after.docs)

    pd_text_facts = _load_text_facts(before_paths)
    after_text_facts = _load_text_facts(after_paths)
    all_text_facts = pd_text_facts + after_text_facts

    composition_entries = extract_composition_entries(all_text_facts)
    composition_refs = find_document_references(all_text_facts)
    composition_supplied = _supplied_documents(
        before_paths + after_paths,
        (before_names or [Path(p).name for p in before_paths])
        + (after_names or [Path(p).name for p in after_paths]),
    )
    composition_result = check_completeness(composition_entries, composition_refs, composition_supplied)

    use_llm = llm_config is not None and bool(llm_config.api_key) and llm_config.provider not in ("", "local")

    if use_llm:
        pd_requirements = extract_requirements_llm(pd_text_facts, llm_config)  # type: ignore[arg-type]
    else:
        pd_requirements = extract_requirements(pd_text_facts)
        not_run.append("requirements_llm_extract: нет ключа ИИ — извлечение требований узким regex-путём (Г.36)")

    general_requirements = extract_general_requirements(pd_text_facts)

    req_after = [DocumentInput(name="РД", pages=1, text_facts=after_text_facts)]
    req_result = cross_check_requirements(pd_requirements, req_after)
    general_req_result = cross_check_general_requirements(general_requirements, req_after)

    signals: list[Signal] = []
    signals += signals_from_room_cross_check(room_result.findings)
    signals += signals_from_equip_cross_check(equip_result.findings)
    signals += signals_from_requirement_cross_check(req_result.findings)
    signals += [
        Signal(source="composition_registry", domain="document", key=f.designation, detail=f.detail)
        for f in composition_result.findings
    ]

    routing_room_keys = list(room_keys)
    auto_selected = False
    routing_diff_result: Optional[dict] = None
    if not routing_room_keys and use_llm:
        candidates_rooms = sorted({f.room_key for f in room_result.findings})
        routing_room_keys = candidates_rooms[:MAX_AUTO_ROUTING_ROOMS]
        auto_selected = True
    if routing_room_keys:
        routing_diff_result = diff_room_routing(before_paths, after_paths, routing_room_keys)
        signals += signals_from_routing_diff(routing_diff_result)
    else:
        not_run.append(
            "routing_diff: --rooms/room_keys не задан, и авто-выбор недоступен без ключа ИИ "
            "(Г.50) — граф маршрутизации не проверялся"
        )

    not_run.append(
        "ventilation_mo (местные отсосы, Г.58/Г.65): требует ключа ИИ (оба листа только зрением) — не подключено к этому эндпоинту"
    )
    not_run.append(
        "run_page_pair_comparison (прямое сравнение листов зрением, Г.51): требует ключа ИИ, "
        "не заведено в триангуляцию по конструкции — не подключено к этому эндпоинту"
    )

    confirmations = triangulate(signals)
    confirmed = confirmed_only(confirmations)
    candidates = candidates_only(confirmations)
    tickets = build_tickets(candidates)

    verdicts: list[KeyVerdict] = []
    if use_llm and signals:
        verdicts = synthesize_all(signals, llm_config)  # type: ignore[arg-type]
    else:
        not_run.append("verdict_synthesis (Г.61): требует ключа ИИ — сводный вердикт не построен")

    return {
        "valid": True,
        "documents": {
            "before": [d.name for d in before.docs],
            "after": [d.name for d in after.docs],
        },
        "skipped_files": skipped,
        "llm": {
            "used": use_llm,
            "provider": llm_config.provider if llm_config else None,
        },
        "not_run": not_run,
        "rooms": {
            "total_pd": room_result.total_pd_rooms,
            "total_rd": room_result.total_rd_rooms,
            "matched": room_result.matched_rooms,
            "unmatched": room_result.unmatched_rooms,
            "findings": _to_jsonable(room_result.findings),
        },
        "equipment": {
            "total_pd": equip_result.total_pd_equip,
            "total_rd": equip_result.total_rd_equip,
            "matched": equip_result.matched_equip,
            "unmatched": equip_result.unmatched_equip,
            "findings": _to_jsonable(equip_result.findings),
        },
        "composition": {
            "supplied_count": len(composition_supplied),
            "referenced_and_supplied": composition_result.referenced_and_supplied,
            "findings": _to_jsonable(composition_result.findings),
        },
        "requirements": {
            "coded": {
                "total": req_result.total_coded,
                "no_code_total": req_result.total_no_code,
                "confirmed": req_result.coded_confirmed,
                "missing": req_result.coded_missing,
                "findings": _to_jsonable(req_result.findings),
                "source": "llm" if use_llm else "regex",
            },
            "general": {
                "total": general_req_result.total,
                "with_token": general_req_result.with_token,
                "token_confirmed": general_req_result.token_confirmed,
                "token_missing": general_req_result.token_missing,
                "no_token": general_req_result.no_token,
                "findings": _to_jsonable(general_req_result.findings),
            },
        },
        "routing": {
            "room_keys": routing_room_keys,
            "auto_selected": auto_selected,
            "diff": routing_diff_result,
        } if routing_room_keys else None,
        "triangulation": {
            "signals_count": len(signals),
            "confirmed": _to_jsonable(confirmed),
            "candidates": _to_jsonable(candidates),
        },
        "escalation_tickets": _to_jsonable(tickets),
        "verdicts": _to_jsonable(verdicts),
    }
