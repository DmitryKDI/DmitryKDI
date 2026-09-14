"""Generic blind control: table assignments in PD versus entities on RD plans.

The mechanism is registry-driven.  It contains no benchmark room numbers or
expected findings: a table kind participates only when ``table_registry``
describes what entity that table assigns to a room.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Sequence

from .anchors import normalize_room_key
from .llm import LlmConfig
from .matching import DocumentInput
from .room_entity_check import (
    cross_check_entities,
    extract_plan_entities,
    extract_table_page,
    find_distribution_table_pages,
)
from .table_registry import all_known_kinds
from .triangulation import Signal


def _drawing_pages_for_rooms(document: DocumentInput, rooms: set[str]) -> list[int]:
    by_page: dict[int, set[str]] = {}
    for fact in document.room_facts:
        page = int(fact.get("page") or 0)
        key = normalize_room_key(fact.get("key", ""))
        if page > 0 and key:
            by_page.setdefault(page, set()).add(key)
    pages = [
        page for page, keys in by_page.items()
        if keys & rooms and document.page_kinds.get(page) == "drawing"
    ]
    return sorted(pages)


def run_room_entity_controls(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_table_pages: int = 4,
    max_plan_pages: int = 8,
) -> tuple[list[Signal], list[dict]]:
    """Run only controls that can be grounded on both PD table and RD plan."""
    if max_table_pages <= 0 or max_plan_pages <= 0:
        return [], []

    signals: list[Signal] = []
    diagnostics: list[dict] = []
    table_budget = max_table_pages
    plan_budget = max_plan_pages

    kinds = [
        kind for kind in all_known_kinds()
        if kind.entity_name and kind.entity_column and kind.system_column
    ]

    for kind in kinds:
        if table_budget <= 0 or plan_budget <= 0:
            break

        pd_rows: list[dict] = []
        rooms_seen: set[str] = set()
        table_pages_used: list[dict] = []
        errors: list[str] = []

        for file_index, document in enumerate(before_docs):
            if table_budget <= 0:
                break
            if kind.discipline_hint and document.discipline_code not in {None, kind.discipline_hint}:
                continue
            pages = find_distribution_table_pages(document.text_facts, document.pages, kind.kind)
            for page in pages:
                if table_budget <= 0:
                    break
                table_budget -= 1
                result = extract_table_page(before_paths[file_index], page, kind, config)
                if result.get("error"):
                    errors.append(f"PD {document.name} p{page}: {result['error']}")
                    continue
                for raw in result.get("rooms_seen") or []:
                    key = normalize_room_key(raw)
                    if key:
                        rooms_seen.add(key)
                for row in result.get("rooms") or []:
                    if not isinstance(row, dict):
                        continue
                    normalized = dict(row)
                    normalized["room"] = normalize_room_key(row.get("room", ""))
                    if normalized["room"]:
                        rooms_seen.add(normalized["room"])
                        pd_rows.append(normalized)
                table_pages_used.append({
                    "document": document.name,
                    "page": page,
                    "rows": len(result.get("rooms") or []),
                    "rooms_seen": len(result.get("rooms_seen") or []),
                })

        target_rooms = {str(row.get("room") or "") for row in pd_rows if row.get("room")}
        rd_entities: list[dict] = []
        plan_pages_used: list[dict] = []

        if target_rooms:
            candidates: list[tuple[int, int, int]] = []
            for file_index, document in enumerate(after_docs):
                if kind.discipline_hint and document.discipline_code not in {None, kind.discipline_hint}:
                    continue
                for page in _drawing_pages_for_rooms(document, target_rooms):
                    overlap = {
                        normalize_room_key(fact.get("key", ""))
                        for fact in document.room_facts
                        if int(fact.get("page") or 0) == page
                    } & target_rooms
                    candidates.append((-len(overlap), file_index, page))
            candidates.sort()

            for _, file_index, page in candidates:
                if plan_budget <= 0:
                    break
                plan_budget -= 1
                entities = extract_plan_entities(after_paths[file_index], page, kind, config)
                for item in entities:
                    if not isinstance(item, dict):
                        continue
                    normalized = dict(item)
                    normalized["nearest_room"] = normalize_room_key(item.get("nearest_room", ""))
                    rd_entities.append(normalized)
                plan_pages_used.append({
                    "document": after_docs[file_index].name,
                    "page": page,
                    "entities": len(entities),
                })

        findings = cross_check_entities(pd_rows, rd_entities) if pd_rows and plan_pages_used else []
        for finding in findings:
            key = normalize_room_key(finding.room)
            if key:
                signals.append(Signal("room_entity", "room", key, finding.detail))

        if table_pages_used or target_rooms:
            diagnostics.append({
                "kind": kind.kind,
                "discipline": kind.discipline_hint,
                "table_pages": table_pages_used,
                "plan_pages": plan_pages_used,
                "pd_rows": len(pd_rows),
                "rooms_seen": sorted(rooms_seen),
                "target_rooms": sorted(target_rooms),
                "rd_entities": len(rd_entities),
                "findings": [asdict(finding) for finding in findings],
                "errors": errors,
                "status": (
                    "checked" if pd_rows and plan_pages_used
                    else "no_grounded_plan" if pd_rows
                    else "no_assignments"
                ),
            })

    return signals, diagnostics
