"""Escalate strong drawing pairs from deterministic diff to semantic vision."""
from __future__ import annotations

import re
from typing import Sequence

from .anchors import normalize_room_key
from .llm import LlmConfig
from .matching import DocumentInput, match_page_pairs
from .triangulation import Signal
from .vision import compare_page_pair
from .visual_prefilter import visual_change_evidence

_ROOM_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d+)?)(?!\d)")


def _room_keys(document: DocumentInput, page: int) -> set[str]:
    out: set[str] = set()
    for fact in document.room_facts:
        if int(fact.get("page") or 0) != page:
            continue
        key = normalize_room_key(fact.get("key", ""))
        if key:
            out.add(key)
    return out


def _mentioned_rooms(text: str, allowed: set[str]) -> set[str]:
    return {
        normalize_room_key(match)
        for match in _ROOM_NUMBER_RE.findall(text or "")
        if normalize_room_key(match) in allowed
    }


def run_targeted_pair_vision(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_pairs: int = 6,
) -> tuple[list[Signal], list[dict]]:
    """Run semantic vision only for strongly matched, structurally changed drawings."""
    if max_pairs <= 0:
        return [], []

    pairs = match_page_pairs(list(before_docs), list(after_docs))
    strong = [
        pair for pair in pairs
        if pair.page_kind == "drawing"
        and pair.matched_by == "text"
        and not pair.discipline_mismatch
    ]
    strong.sort(key=lambda pair: (-pair.score, pair.before_file_idx, pair.before_page))

    signals: list[Signal] = []
    diagnostics: list[dict] = []
    llm_used = 0

    for pair in strong:
        if llm_used >= max_pairs:
            break
        before_path = before_paths[pair.before_file_idx]
        after_path = after_paths[pair.after_file_idx]
        pair_key = f"{pair.before_file_idx}:{pair.before_page}->{pair.after_file_idx}:{pair.after_page}"

        try:
            evidence = visual_change_evidence(
                before_path, pair.before_page, after_path, pair.after_page
            )
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({
                "pair_key": pair_key,
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "score": pair.score,
                "status": "prefilter_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        base_diag = {
            "pair_key": pair_key,
            "before_page": pair.before_page,
            "after_page": pair.after_page,
            "before_file_index": pair.before_file_idx,
            "after_file_index": pair.after_file_idx,
            "score": round(float(pair.score), 6),
            "diff_ratio": evidence.get("diff_ratio"),
            "changed_cells": evidence.get("changed_cells"),
            "local_cluster": bool(evidence.get("local_cluster")),
            "hot_zone": evidence.get("hot_zone"),
        }
        if not evidence.get("significant"):
            diagnostics.append({**base_diag, "status": "visually_same"})
            continue

        llm_used += 1
        before_doc = before_docs[pair.before_file_idx]
        after_doc = after_docs[pair.after_file_idx]
        clip = evidence.get("hot_zone") if evidence.get("local_cluster") else None
        try:
            result = compare_page_pair(
                before_path,
                pair.before_page,
                after_path,
                pair.after_page,
                config,
                context=(
                    f"раздел {before_doc.discipline_code or '?'}; "
                    f"детерминированный raster-diff локализовал изменение"
                ),
                discipline=before_doc.discipline_code,
                clip_frac=tuple(clip) if clip else None,
            )
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({
                **base_diag,
                "status": "vision_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        significant = result.get("significant") if isinstance(result, dict) else None
        significant = significant if isinstance(significant, list) else []
        items = [item for item in significant if isinstance(item, dict) and item.get("change")]
        if not items:
            diagnostics.append({**base_diag, "status": "no_semantic_change"})
            continue

        changes = [str(item.get("change") or "").strip() for item in items]
        detail = " | ".join(changes[:5])
        signals.extend([
            Signal(
                source="raster_diff",
                domain="page_pair",
                key=pair_key,
                detail=(
                    f"структурное отличие: ratio={evidence.get('diff_ratio')}; "
                    f"cells={evidence.get('changed_cells')}"
                ),
            ),
            Signal(source="vision_pair", domain="page_pair", key=pair_key, detail=detail),
        ])

        shared_rooms = _room_keys(before_doc, pair.before_page) & _room_keys(after_doc, pair.after_page)
        mentioned: set[str] = set()
        for item in items:
            text = " ".join(
                str(item.get(field) or "")
                for field in ("label", "change", "field_check", "where")
            )
            mentioned |= _mentioned_rooms(text, shared_rooms)
        for room in sorted(mentioned):
            signals.append(Signal("vision", "room", room, detail))

        diagnostics.append({
            **base_diag,
            "status": "significant",
            "significant_total": len(items),
            "rooms_shared": sorted(shared_rooms),
            "rooms_mentioned": sorted(mentioned),
            "changes": changes[:5],
        })

    return signals, diagnostics
