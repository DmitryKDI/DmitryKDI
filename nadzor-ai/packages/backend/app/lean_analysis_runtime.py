"""Lean active PD -> RD/ID analysis runtime.

There are only two discovery adapters and one semantic contract:
1. TEXT requirement PD -> candidate RD evidence -> universal semantic contract.
2. DRAWING PD -> candidate drawing RD -> the same evidence states/guardrails.

Legacy registries, routing-diff, verdict synthesis and mandatory triangulation
remain compatibility code only and are not executed here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Optional

import pymupdf

from .anchors import normalize_room_key
from .control_pair_vision import run_targeted_pair_vision
from .facts_store import facts_for
from .llm import LlmConfig
from .matching import DocumentInput
from .requirement_llm_extract import extract_requirements_llm
from .requirement_registry import extract_requirements
from .semantic_requirement_runtime import check_requirements_semantic


@dataclass
class DocumentLoadResult:
    docs: list[DocumentInput] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _elapsed(started: float) -> float:
    return round(perf_counter() - started, 4)


def _load_documents(paths: list[str], names: Optional[list[str]] = None) -> DocumentLoadResult:
    out: list[DocumentInput] = []
    skipped: list[str] = []
    for index, raw_path in enumerate(paths):
        source = Path(raw_path)
        display_name = names[index] if names and index < len(names) else source.name
        if not source.is_file():
            skipped.append(f"{display_name}: не найден")
            continue
        try:
            facts = facts_for(str(source), display_name)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{display_name}: {type(exc).__name__}: {exc}")
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
    return DocumentLoadResult(out, skipped)


def _load_text_facts(paths: list[str], names: Optional[list[str]] = None) -> list[dict]:
    out: list[dict] = []
    fact_id = 0
    for index, raw_path in enumerate(paths):
        source = Path(raw_path)
        if not source.is_file():
            continue
        display_name = names[index] if names and index < len(names) else source.name
        try:
            doc = pymupdf.open(str(source))
        except Exception:  # noqa: BLE001
            continue
        try:
            for page_index in range(doc.page_count):
                text = doc[page_index].get_text("text").strip()
                if not text:
                    continue
                fact_id += 1
                out.append({
                    "fact_id": fact_id,
                    "page": page_index + 1,
                    "text": text,
                    "document": display_name,
                    "section": "",
                })
        finally:
            doc.close()
    return out


def _room_index(paths: list[str], names: Optional[list[str]] = None) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for file_index, raw_path in enumerate(paths):
        source = Path(raw_path)
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
            key = normalize_room_key(fact.get("key", ""))
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


def _compliance_payload(result) -> dict:
    return {
        "counts": dict(result.counts or {}),
        "not_run": list(result.not_run or []),
        "diagnostics": dict(result.diagnostics or {}),
        "items": [asdict(item) for item in result.items],
    }


def _pair_summary(diagnostics: list[dict]) -> dict:
    pair_rows = [
        row for row in diagnostics
        if isinstance(row, dict) and row.get("control_type") == "page_pair"
    ]
    counts: dict[str, int] = {}
    for row in pair_rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    coverage = next(
        (
            row for row in reversed(diagnostics)
            if isinstance(row, dict) and row.get("control_type") == "coverage"
        ),
        {},
    )
    return {"counts": counts, "results": pair_rows, "coverage": coverage}


def _pair_candidates(pair_payload: dict) -> list[dict]:
    out: list[dict] = []
    for row in pair_payload.get("results") or []:
        for finding in row.get("unverified_candidates") or []:
            out.append({
                "source": "vision_pair_candidate",
                "pair_key": row.get("pair_key"),
                "before_page": row.get("before_page"),
                "after_page": row.get("after_page"),
                "status": row.get("status"),
                "finding": finding,
            })
    return out


def _requirement_candidates(payload: dict) -> list[dict]:
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    out: list[dict] = []
    for row in diagnostics.get("results") or []:
        for finding in row.get("candidate_findings") or []:
            out.append({
                "source": "requirement_candidate",
                "requirement_index": row.get("requirement_index"),
                "pd_document": row.get("pd_document"),
                "pd_page": row.get("pd_page"),
                "final_state": row.get("final_state"),
                "finding": finding,
            })
    return out


def run_lean_analysis(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: Optional[list[str]] = None,
    llm_config: Optional[LlmConfig] = None,
    before_names: Optional[list[str]] = None,
    after_names: Optional[list[str]] = None,
) -> dict:
    """Run the active blind semantic architecture.

    `room_keys` stays only for API compatibility. Manually supplied rooms must
    never change whether semantic comparison executes.
    """
    del room_keys
    timings: dict[str, float] = {}
    total_started = perf_counter()

    stage = perf_counter()
    before = _load_documents(before_paths, before_names)
    after = _load_documents(after_paths, after_names)
    timings["load_documents"] = _elapsed(stage)
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
            "active_architecture": "universal_semantic_contract",
        }

    use_llm = bool(
        llm_config is not None
        and llm_config.api_key
        and llm_config.provider not in ("", "local")
    )
    call_failures: list[str] = []

    stage = perf_counter()
    pd_text_facts = _load_text_facts(before_paths, before_names)
    timings["load_pd_text"] = _elapsed(stage)

    stage = perf_counter()
    if use_llm:
        def _on_extract_error(page: int, exc: Exception) -> None:
            call_failures.append(f"requirements_llm_extract стр.{page}+: {exc!r}")

        requirements = extract_requirements_llm(
            pd_text_facts,
            llm_config,  # type: ignore[arg-type]
            on_chunk_error=_on_extract_error,
        )
        requirement_source = "llm"
    else:
        requirements = extract_requirements(pd_text_facts)
        requirement_source = "regex_fallback"
    timings["requirements_extract"] = _elapsed(stage)

    rd_sources = [
        (str(path), after_names[index] if after_names and index < len(after_names) else Path(path).name)
        for index, path in enumerate(after_paths)
        if Path(path).is_file()
    ]

    stage = perf_counter()
    compliance = check_requirements_semantic(
        requirements,
        rd_sources,
        llm_config if use_llm else None,
        room_index=_room_index(after_paths, after_names),
    )
    timings["requirement_semantic_compare"] = _elapsed(stage)

    stage = perf_counter()
    pair_signals = []
    pair_diagnostics: list[dict] = []
    if use_llm:
        try:
            pair_signals, pair_diagnostics = run_targeted_pair_vision(
                before.docs,
                after.docs,
                before_paths,
                after_paths,
                llm_config,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001
            call_failures.append(f"semantic_pair_runtime: {type(exc).__name__}: {exc}")
    timings["drawing_semantic_compare"] = _elapsed(stage)
    timings["total"] = _elapsed(total_started)

    requirement_payload = _compliance_payload(compliance)
    pair_payload = _pair_summary(pair_diagnostics)
    semantic_findings = [
        {
            "source": signal.source,
            "domain": signal.domain,
            "key": signal.key,
            "detail": signal.detail,
        }
        for signal in pair_signals
        if signal.source in {"vision", "vision_pair"}
    ]
    semantic_candidates = [
        *_requirement_candidates(requirement_payload),
        *_pair_candidates(pair_payload),
    ]

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
            "call_failures": call_failures,
        },
        "not_run": (["semantic checks: нет ключа ИИ"] if not use_llm else []),
        "performance": {
            "duration_seconds": timings["total"],
            "stages_seconds": timings,
        },
        "active_architecture": (
            "PD intent -> candidate RD evidence -> scope/inventory -> region discovery -> "
            "local verification -> universal semantic evidence contract"
        ),
        "semantic_contract": {
            "states": [
                "OBSERVED_CONTRADICTION",
                "NOT_OBSERVED_ON_THIS_EVIDENCE",
                "WRONG_OR_INSUFFICIENT_SCOPE",
                "APPEARS_COMPLIANT",
            ],
            "absence_from_not_observed_forbidden": True,
            "whole_page_finding_final": False,
        },
        "requirements": {
            "source": requirement_source,
            "total": len(requirements),
            "compliance": requirement_payload,
        },
        "vision_requirements": requirement_payload,
        "pair_vision": pair_payload,
        "semantic_findings": semantic_findings,
        "semantic_candidates": semantic_candidates,
        "rooms": {"active": False, "findings": [], "signals_total": 0},
        "equipment": {"active": False, "findings": [], "signals_total": 0},
        "composition": {"active": False, "findings": []},
        "routing": None,
        "triangulation": {
            "active": False,
            "signals_count": len(semantic_findings),
            "confirmed": semantic_findings,
            "candidates": semantic_candidates,
        },
        "verdicts": [],
        "escalation_tickets": [],
        "legacy_runtime": {
            "room_registry": False,
            "equipment_registry": False,
            "composition_registry": False,
            "routing_diff": False,
            "general_requirement_filter": False,
            "legacy_compliance_ladder": False,
            "verdict_synthesis": False,
            "mandatory_triangulation": False,
        },
    }
