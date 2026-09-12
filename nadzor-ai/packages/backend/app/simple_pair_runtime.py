"""Simple high-recall drawing comparison runtime.

Active semantic flow is intentionally small:

    pair discovery -> whole-page compare -> model-driven zoom -> findings

Rooms, axes, equipment tags, geometry and raster are routing/diagnostic hints only.
They never decide whether an accepted candidate pair is semantically compared.
The older specialized room/non-room routines remain available as helpers, but are
not part of this active path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .control_pair_candidates import candidate_pairs
from .control_pair_runtime import (
    MAX_GRAPHICAL_CANDIDATE_PAIRS,
    semantic_scope_compare,
    visual_change_evidence,
)
from .llm import LlmConfig
from .matching import DocumentInput
from .triangulation import Signal
from .vision_page_compare import normalize_regions


def _comparability(result: dict) -> str:
    value = str(result.get("comparability") or "").strip().casefold()
    if value in {"high", "medium", "low"}:
        return value
    return "low" if result.get("comparable") is False else "medium"


def _differences(result: dict) -> list[dict]:
    raw = result.get("differences") if isinstance(result, dict) else []
    if not isinstance(raw, list):
        return []
    return [
        item
        for item in raw
        if isinstance(item, dict) and str(item.get("change") or "").strip()
    ]


@dataclass
class _State:
    pair: object
    before_path: str
    after_path: str
    before_doc: DocumentInput
    after_doc: DocumentInput
    base: dict
    whole_page_done: bool = False
    comparability: str = "low"
    unclear_reason: str = ""
    regions: list[dict] = field(default_factory=list)
    regions_seen: int = 0
    findings: list[dict] = field(default_factory=list)

    def final_status(self) -> tuple[str, str]:
        if self.findings:
            return "significant", self.unclear_reason
        if not self.whole_page_done:
            return "not_compared", self.unclear_reason or "лист целиком не просмотрен"
        if self.comparability == "low":
            return "not_compared", self.unclear_reason or "недостаточная читаемость"
        unseen_high = [
            region for region in self.regions[self.regions_seen:]
            if str(region.get("priority") or "").casefold() == "high"
        ]
        if unseen_high:
            return "not_compared", "не все high-priority зоны осмотрены"
        return "compared_no_candidate", self.unclear_reason


def _compare(state: _State, config: LlmConfig, clip=None) -> dict:
    pair = state.pair
    result = semantic_scope_compare(
        state.before_path,
        pair.before_page,
        state.after_path,
        pair.after_page,
        state.before_doc,
        state.after_doc,
        pair.shared_rooms,
        config,
        clip=clip,
    )
    return result if isinstance(result, dict) else {}


def run_targeted_pair_vision(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_pairs: int = 6,
):
    """Compare accepted candidate pairs with one universal semantic path.

    `candidate_pairs` is allowed to rank candidates. Once a pair is selected,
    however, it always receives a whole-page semantic call while budget remains.
    Raster/anchors are never semantic gates. Local inspection is requested by the
    model itself through `candidate_regions`, then scheduled round-robin.
    """
    signals: list[Signal] = []
    diagnostics: list[dict] = []
    if max_pairs <= 0:
        return signals, diagnostics

    all_pairs = candidate_pairs(before_docs, after_docs)
    pair_limit = min(MAX_GRAPHICAL_CANDIDATE_PAIRS, max_pairs)
    pairs = all_pairs[:pair_limit]
    states: list[_State] = []

    # Stage 1: every accepted pair gets a whole-page semantic comparison.
    for pair in pairs:
        before_path = before_paths[pair.before_file_idx]
        after_path = after_paths[pair.after_file_idx]
        before_doc = before_docs[pair.before_file_idx]
        after_doc = after_docs[pair.after_file_idx]
        try:
            raster = visual_change_evidence(
                before_path, pair.before_page, after_path, pair.after_page
            )
        except Exception as exc:  # diagnostic only
            raster = {"significant": False, "error": f"{type(exc).__name__}: {exc}"}

        base = {
            "control_type": "page_pair",
            "pair_key": pair.key,
            "before_page": pair.before_page,
            "after_page": pair.after_page,
            "before_file_index": pair.before_file_idx,
            "after_file_index": pair.after_file_idx,
            "matched_by": pair.matched_by,
            "routing_score": round(pair.score, 6),
            "rooms_shared": list(pair.shared_rooms),
            "anchors_shared": list(getattr(pair, "shared_anchors", ()) or ()),
            "raster_significant": bool(raster.get("significant")),
            "diff_ratio": raster.get("diff_ratio"),
        }
        state = _State(pair, before_path, after_path, before_doc, after_doc, base)
        states.append(state)
        try:
            result = _compare(state, config)
        except Exception as exc:
            state.unclear_reason = f"whole-page vision failed: {type(exc).__name__}: {exc}"
            diagnostics.append({
                **base,
                "control_type": "whole_page_vision",
                "status": "vision_error",
                "error": state.unclear_reason,
            })
            continue

        state.whole_page_done = True
        state.comparability = _comparability(result)
        state.unclear_reason = str(result.get("unclear_reason") or "")
        state.regions = normalize_regions(result.get("candidate_regions"))
        state.findings.extend(_differences(result))

    # Stage 2: model-driven zoom only. Fair round-robin prevents one pair from
    # consuming all local calls before the other pairs are inspected.
    max_zooms_per_pair = 2
    for round_index in range(max_zooms_per_pair):
        for state in states:
            if round_index >= len(state.regions):
                continue
            region = state.regions[round_index]
            try:
                result = _compare(state, config, clip=region["rd_bbox_norm"])
            except Exception as exc:
                diagnostics.append({
                    **state.base,
                    "control_type": "model_region_zoom",
                    "status": "vision_error",
                    "region_index": round_index,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            state.regions_seen = round_index + 1
            local_comparability = _comparability(result)
            if local_comparability == "high":
                state.comparability = "high"
            elif local_comparability == "medium" and state.comparability == "low":
                state.comparability = "medium"
            state.findings.extend(_differences(result))

    for state in states:
        status, reason = state.final_status()
        record = {
            **state.base,
            "status": status,
            "whole_page_done": state.whole_page_done,
            "comparability": state.comparability,
            "candidate_regions_total": len(state.regions),
            "candidate_regions_checked": state.regions_seen,
            "unclear_reason": reason,
        }
        if state.findings:
            detail = " | ".join(
                str(item.get("change") or "").strip()
                for item in state.findings[:8]
            )
            signals.append(Signal("vision_pair", "page_pair", state.pair.key, detail))
            record["differences_total"] = len(state.findings)
        diagnostics.append(record)

    diagnostics.append({
        "control_type": "coverage",
        "status": "complete" if len(all_pairs) <= len(pairs) else "candidate_budget",
        "candidate_pairs_total": len(all_pairs),
        "candidate_pairs_checked": len(pairs),
        "semantic_architecture": "pair_discovery->whole_page->model_zoom",
        "anchors_are_gates": False,
        "raster_is_gate": False,
    })
    return signals, diagnostics
