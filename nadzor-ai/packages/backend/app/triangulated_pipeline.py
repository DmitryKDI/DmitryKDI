"""Сводный движок «Точек контроля» для HTTP-сервиса.

Пайплайн объединяет дешёвые детерминированные источники, требования из ПД,
точечную vision-проверку требований, pixel-diff + semantic vision сильных
пар чертежей и маршрутизацию. Подтверждение требует минимум два разных
источника; дорогие шаги запускаются только после дешёвого отбора.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from time import perf_counter
from typing import Optional

import pymupdf

from .composition_registry import (
    SuppliedDocument,
    check_completeness,
    extract_composition_entries,
    find_document_references,
)
from .control_pair_vision import run_targeted_pair_vision
from .equip_cross_check import cross_check_equipment
from .escalation import build_tickets
from .facts_store import facts_for
from .llm import LlmConfig
from .matching import DocumentInput
from .requirement_cross_check import cross_check_general_requirements, cross_check_requirements
from .requirement_llm_extract import extract_requirements_llm
from .requirement_llm_filter import classify_general_requirements
from .requirement_registry import extract_general_requirements, extract_requirements
from .room_cross_check import cross_check_rooms
from .routing_diff import diff_room_routing
from .stamp import read_stamp
from .triangulation import (
    Signal,
    candidates_only,
    confirmed_only,
    signals_from_equip_cross_check,
    signals_from_requirement_cross_check,
    signals_from_room_cross_check,
    signals_from_routing_diff,
    signals_from_visual_requirement_checks,
    triangulate,
)
from .verdict_synthesis import KeyVerdict, synthesize_all
from .vision_page_compare import check_visual_candidates


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_AUTO_ROUTING_ROOMS = _positive_int_env("NADZOR_MAX_AUTO_ROUTING_ROOMS", 8, 0, 50)
MAX_VISUAL_REQUIREMENT_FINDINGS = _positive_int_env(
    "NADZOR_MAX_VISUAL_REQUIREMENT_FINDINGS", 12, 0, 50
)
MAX_VISUAL_PAGES_PER_FINDING = _positive_int_env(
    "NADZOR_MAX_VISUAL_PAGES_PER_FINDING", 2, 1, 5
)
# Полноценное сравнение двух изображений листов дороже pixel-diff, поэтому
# оно выполняется только для нескольких сильных пар, прошедших предфильтр.
MAX_CONTROL_PAIR_VISION = _positive_int_env(
    "NADZOR_MAX_CONTROL_PAIR_VISION", 6, 0, 20
)


def _to_jsonable(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


@dataclass
class DocumentLoadResult:
    docs: list[DocumentInput] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _elapsed(started: float) -> float:
    return round(perf_counter() - started, 4)


def _load_documents(paths: list[str], names: Optional[list[str]] = None) -> DocumentLoadResult:
    out: list[DocumentInput] = []
    skipped: list[str] = []
    for index, path in enumerate(paths):
        source = Path(path)
        display_name = names[index] if names and index < len(names) else source.name
        if not source.is_file():
            skipped.append(f"{display_name}: не найден")
            continue
        try:
            facts = facts_for(str(source), display_name)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{display_name}: {exc}")
            continue
        out.append(DocumentInput(
            name=display_name,
            pages=facts.pages,
            text_facts=facts.text_facts,
            room_facts=facts.room_facts,
            discipline_code=getattr(facts, "discipline_code", None),
            page_kinds=facts.page_kinds,
            equipment_facts=facts.equipment_facts,
            balance_facts=facts.balance_facts,
        ))
    return DocumentLoadResult(docs=out, skipped=skipped)


def _load_text_facts(paths: list[str]) -> list[dict]:
    out: list[dict] = []
    for path in paths:
        source = Path(path)
        if not source.is_file():
            continue
        try:
            doc = pymupdf.open(str(source))
        except Exception:  # noqa: BLE001
            continue
        try:
            for index in range(doc.page_count):
                text = doc[index].get_text("text").strip()
                if text:
                    out.append({"page": index + 1, "text": text})
        finally:
            doc.close()
    return out


def _supplied_documents(paths: list[str], names: Optional[list[str]] = None) -> list[SuppliedDocument]:
    out: list[SuppliedDocument] = []
    for index, path in enumerate(paths):
        display_name = names[index] if names and index < len(names) else Path(path).name
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
        except Exception:  # noqa: BLE001
            pass
        out.append(SuppliedDocument(filename=display_name, shifrs=shifrs))
    return out


def _room_index_for_vision(paths: list[str], names: Optional[list[str]] = None) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for file_index, path in enumerate(paths):
        source = Path(path)
        if not source.is_file():
            continue
        display_name = names[file_index] if names and file_index < len(names) else source.name
        try:
            facts = facts_for(str(source), display_name)
        except Exception:  # noqa: BLE001
            continue
        text_by_page = {
            int(item.get("page") or 0): str(item.get("text") or "")
            for item in facts.text_facts
        }
        for fact in facts.room_facts:
            key = str(fact.get("key") or "").strip()
            page = int(fact.get("page") or 0)
            if not key or page <= 0:
                continue
            index.setdefault(key, []).append({
                "path": str(source),
                "page": page,
                "name": display_name,
                "text": text_by_page.get(page, ""),
            })
    return index


def _visual_requirement_candidates(findings: list) -> tuple[list, int]:
    selected: list = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    eligible = [
        finding for finding in findings
        if getattr(finding, "finding_type", "") == "no_code_visual_check_needed"
        and getattr(finding, "rooms", None)
    ]
    for finding in eligible:
        key = (
            str(getattr(finding, "sentence_pd", "")).strip(),
            tuple(str(room) for room in getattr(finding, "rooms", []) if str(room).strip()),
        )
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        selected.append(finding)
        if len(selected) >= MAX_VISUAL_REQUIREMENT_FINDINGS:
            break
    return selected, len(eligible)


def run_triangulated_analysis(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: Optional[list[str]] = None,
    llm_config: Optional[LlmConfig] = None,
    before_names: Optional[list[str]] = None,
    after_names: Optional[list[str]] = None,
) -> dict:
    room_keys = list(room_keys or [])
    not_run: list[str] = []
    timings: dict[str, float] = {}
    total_started = perf_counter()

    stage_started = perf_counter()
    before = _load_documents(before_paths, before_names)
    after = _load_documents(after_paths, after_names)
    timings["load_documents"] = _elapsed(stage_started)
    skipped = before.skipped + after.skipped

    if not before.docs or not after.docs:
        timings["total"] = _elapsed(total_started)
        return {
            "valid": False,
            "reason": (
                f"прогон недействителен: сторона "
                f"{'ПД' if not before.docs else 'РД'} пуста "
                f"(ПД {len(before.docs)}/{len(before_paths)}, РД {len(after.docs)}/{len(after_paths)})"
            ),
            "skipped_files": skipped,
            "performance": {"stages_seconds": timings, "duration_seconds": timings["total"]},
        }

    stage_started = perf_counter()
    room_result = cross_check_rooms(before.docs, after.docs)
    equip_result = cross_check_equipment(before.docs, after.docs)
    timings["registries"] = _elapsed(stage_started)

    stage_started = perf_counter()
    pd_text_facts = _load_text_facts(before_paths)
    after_text_facts = _load_text_facts(after_paths)
    all_text_facts = pd_text_facts + after_text_facts
    composition_entries = extract_composition_entries(all_text_facts)
    composition_refs = find_document_references(all_text_facts)
    supplied_names = (
        (before_names or [Path(path).name for path in before_paths])
        + (after_names or [Path(path).name for path in after_paths])
    )
    composition_supplied = _supplied_documents(before_paths + after_paths, supplied_names)
    composition_result = check_completeness(composition_entries, composition_refs, composition_supplied)
    timings["text_and_composition"] = _elapsed(stage_started)

    use_llm = (
        llm_config is not None
        and bool(llm_config.api_key)
        and llm_config.provider not in ("", "local")
    )
    llm_call_failures: list[str] = []

    stage_started = perf_counter()
    if use_llm:
        def _on_extract_error(page: int, exc: Exception) -> None:
            llm_call_failures.append(f"requirements_llm_extract стр.{page}+: {exc!r}")

        pd_requirements = extract_requirements_llm(
            pd_text_facts, llm_config, on_chunk_error=_on_extract_error  # type: ignore[arg-type]
        )
    else:
        pd_requirements = extract_requirements(pd_text_facts)
        not_run.append("requirements_llm_extract: нет ключа ИИ — используется узкий regex-путь")
    timings["requirements_extract"] = _elapsed(stage_started)

    stage_started = perf_counter()
    general_requirements = extract_general_requirements(pd_text_facts)
    general_verdicts = None
    general_requirements_for_check = general_requirements
    if use_llm:
        def _on_filter_error(batch: list, exc: Exception) -> None:
            first_page = batch[0].page if batch else -1
            llm_call_failures.append(
                f"requirement_llm_filter стр.{first_page}+ ({len(batch)} шт.): {exc!r}"
            )

        general_verdicts = classify_general_requirements(
            general_requirements, llm_config, on_batch_error=_on_filter_error  # type: ignore[arg-type]
        )
        # В cross-check идут только требования, которые смысловой фильтр
        # действительно признал техническими требованиями.
        general_requirements_for_check = [
            verdict.requirement for verdict in general_verdicts if verdict.is_requirement
        ]
    else:
        not_run.append("requirement_llm_filter: нет ключа ИИ")
    timings["requirements_filter"] = _elapsed(stage_started)

    stage_started = perf_counter()
    req_after = [DocumentInput(name="РД", pages=1, text_facts=after_text_facts)]
    req_result = cross_check_requirements(pd_requirements, req_after)
    general_req_result = cross_check_general_requirements(general_requirements_for_check, req_after)
    timings["requirements_cross_check"] = _elapsed(stage_started)

    signals: list[Signal] = []
    signals += signals_from_room_cross_check(room_result.findings)
    signals += signals_from_equip_cross_check(equip_result.findings)
    signals += signals_from_requirement_cross_check(req_result.findings)
    signals += [
        Signal(
            source="composition_registry", domain="document",
            key=finding.designation, detail=finding.detail,
        )
        for finding in composition_result.findings
    ]

    # 1) Требование из ПД проверяется зрением только на листах РД, где есть
    # указанные помещения. Это путь для «тёплый пол должен быть, но на плане нет».
    stage_started = perf_counter()
    visual_results: list[dict] = []
    visual_candidates, visual_eligible_total = _visual_requirement_candidates(req_result.findings)
    if use_llm and visual_candidates and MAX_VISUAL_REQUIREMENT_FINDINGS > 0:
        rd_room_index = _room_index_for_vision(after_paths, after_names)
        visual_results = check_visual_candidates(
            visual_candidates,
            rd_room_index,
            llm_config,  # type: ignore[arg-type]
            max_pages_per_finding=MAX_VISUAL_PAGES_PER_FINDING,
        )
        signals += signals_from_visual_requirement_checks(visual_results)
        if visual_eligible_total > len(visual_candidates):
            not_run.append(
                f"targeted_requirement_vision: проверено {len(visual_candidates)} из "
                f"{visual_eligible_total} из-за лимита"
            )
    elif use_llm and visual_eligible_total and MAX_VISUAL_REQUIREMENT_FINDINGS == 0:
        not_run.append("targeted_requirement_vision: отключено лимитом 0")
    elif not use_llm and visual_eligible_total:
        not_run.append("targeted_requirement_vision: требует ключа ИИ")
    timings["targeted_requirement_vision"] = _elapsed(stage_started)

    # 2) Сильные drawing-пары проходят сначала бесплатный pixel-diff. Только
    # визуально отличающиеся листы эскалируются в semantic vision. Это путь
    # для чисто графических изменений, которых нет в тексте требования.
    stage_started = perf_counter()
    pair_vision_results: list[dict] = []
    if use_llm and MAX_CONTROL_PAIR_VISION > 0:
        pair_signals, pair_vision_results = run_targeted_pair_vision(
            before.docs,
            after.docs,
            before_paths,
            after_paths,
            llm_config,  # type: ignore[arg-type]
            max_pairs=MAX_CONTROL_PAIR_VISION,
        )
        signals += pair_signals
    elif use_llm:
        not_run.append("targeted_pair_vision: отключено лимитом 0")
    else:
        not_run.append("targeted_pair_vision: требует ключа ИИ")
    timings["targeted_pair_vision"] = _elapsed(stage_started)

    # 3) Routing только по сильным комнатным кандидатам, а не по каждому
    # сырому name_changed/missing_in_pd из реестров.
    stage_started = perf_counter()
    routing_room_keys = list(dict.fromkeys(str(key) for key in room_keys if str(key).strip()))
    auto_selected = False
    routing_diff_result: Optional[dict] = None
    if not routing_room_keys and use_llm and MAX_AUTO_ROUTING_ROOMS > 0:
        strong_room_keys = sorted({
            signal.key
            for signal in signals
            if signal.domain == "room"
            and signal.source in {"requirement_prose", "vision", "room_registry"}
        })
        routing_room_keys = strong_room_keys[:MAX_AUTO_ROUTING_ROOMS]
        auto_selected = bool(routing_room_keys)
    if routing_room_keys:
        routing_diff_result = diff_room_routing(before_paths, after_paths, routing_room_keys)
        signals += signals_from_routing_diff(routing_diff_result)
    elif not use_llm:
        not_run.append("routing_diff: room_keys не заданы и нет ключа ИИ")
    timings["routing"] = _elapsed(stage_started)

    # Отдельный room_entity_check остаётся будущей специализированной
    # проверкой таблицы назначений; основной графический blind spot теперь
    # закрывается targeted_requirement_vision + targeted_pair_vision.
    not_run.append("room_entity_check: отдельная сверка таблицы назначений с планом пока не подключена")

    stage_started = perf_counter()
    confirmations = triangulate(signals)
    confirmed = confirmed_only(confirmations)
    candidates = candidates_only(confirmations)
    tickets = build_tickets(candidates)
    timings["triangulation"] = _elapsed(stage_started)

    stage_started = perf_counter()
    verdicts: list[KeyVerdict] = []
    # page_pair уже подтверждена pixel_diff + semantic vision; третий LLM-свод
    # не добавляет независимости и только тратит время. Свод нужен объектным
    # ключам room/equipment/requirement_code/document.
    confirmed_keys = {
        (item.domain, item.key) for item in confirmed if item.domain != "page_pair"
    }
    if use_llm and confirmed_keys:
        verdicts = synthesize_all(
            signals, llm_config, only_keys=confirmed_keys  # type: ignore[arg-type]
        )
    elif use_llm and not confirmed:
        not_run.append("verdict_synthesis: нет объектов, подтверждённых 2+ источниками")
    timings["verdict_synthesis"] = _elapsed(stage_started)
    timings["total"] = _elapsed(total_started)

    visual_counts = {"absent": 0, "confirmed": 0, "unclear": 0}
    for item in visual_results:
        verdict = str(item.get("verdict") or "unclear")
        if verdict in visual_counts:
            visual_counts[verdict] += 1

    pair_status_counts: dict[str, int] = {}
    for item in pair_vision_results:
        status = str(item.get("status") or "unknown")
        pair_status_counts[status] = pair_status_counts.get(status, 0) + 1

    return {
        "valid": True,
        "documents": {
            "before": [doc.name for doc in before.docs],
            "after": [doc.name for doc in after.docs],
        },
        "skipped_files": skipped,
        "llm": {
            "used": use_llm,
            "provider": llm_config.provider if llm_config else None,
            "call_failures": llm_call_failures,
        },
        "not_run": not_run,
        "performance": {
            "duration_seconds": timings["total"],
            "stages_seconds": timings,
        },
        "rooms": {
            "total_pd": room_result.total_pd_rooms,
            "total_rd": room_result.total_rd_rooms,
            "matched": room_result.matched_rooms,
            "unmatched": room_result.unmatched_rooms,
            "findings": _to_jsonable(room_result.findings),
            "signals_total": len(signals_from_room_cross_check(room_result.findings)),
        },
        "equipment": {
            "total_pd": equip_result.total_pd_equip,
            "total_rd": equip_result.total_rd_equip,
            "matched": equip_result.matched_equip,
            "unmatched": equip_result.unmatched_equip,
            "findings": _to_jsonable(equip_result.findings),
            "signals_total": len(signals_from_equip_cross_check(equip_result.findings)),
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
                "raw_total": len(general_requirements),
                "total": general_req_result.total,
                "with_token": general_req_result.with_token,
                "token_confirmed": general_req_result.token_confirmed,
                "token_missing": general_req_result.token_missing,
                "no_token": general_req_result.no_token,
                "findings": _to_jsonable(general_req_result.findings),
                "llm_filter": {
                    "used": general_verdicts is not None,
                    "kept": sum(1 for item in general_verdicts if item.is_requirement)
                    if general_verdicts is not None else None,
                    "dropped_as_noise": _to_jsonable(
                        [item for item in general_verdicts if not item.is_requirement]
                    ) if general_verdicts is not None else [],
                },
            },
        },
        "vision_requirements": {
            "eligible_total": visual_eligible_total,
            "checked_total": len(visual_results),
            "max_findings": MAX_VISUAL_REQUIREMENT_FINDINGS,
            "max_pages_per_finding": MAX_VISUAL_PAGES_PER_FINDING,
            "counts": visual_counts,
            "results": visual_results,
        },
        "pair_vision": {
            "max_llm_pairs": MAX_CONTROL_PAIR_VISION,
            "counts": pair_status_counts,
            "results": pair_vision_results,
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
