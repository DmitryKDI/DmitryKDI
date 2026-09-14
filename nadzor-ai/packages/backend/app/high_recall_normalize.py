from __future__ import annotations

from .anchors import normalize_room_key

_CATEGORIES = {"inventory", "topology", "connection", "parameter", "scale_shift", "drawing_type_mismatch"}
_UNCERTAINTY = {"occlusion", "blur", "low_contrast", "overprint", "fold", "cut", "poor_registration", "dense_hatching", "small_stroke", "unknown_symbols", "cropped_connection", "drawing_type_mismatch"}
_COMPARABILITY = {"high", "medium", "low"}


def _candidate(item):
    if not isinstance(item, dict):
        return None
    desc = str(item.get("description") or "").strip()
    if not desc:
        return None
    cat = str(item.get("category") or "topology").casefold()
    if cat not in _CATEGORIES:
        cat = "topology"
    side = str(item.get("side") or "both").casefold()
    if side not in {"pd", "rd", "both"}:
        side = "both"
    return {
        "category": cat,
        "side": side,
        "description": desc,
        "location_hint": str(item.get("location_hint") or "").strip() or None,
    }


def _comparability(value, default: str = "low") -> str:
    normalized = str(value or default).casefold()
    return normalized if normalized in _COMPARABILITY else default


def normalize_high_recall(result: dict, room: str) -> dict:
    candidates = [x for x in (_candidate(i) for i in (result.get("candidate_differences") or [])) if x]
    coverage = [
        str(x).casefold() for x in (result.get("coverage") or [])
        if str(x).casefold() in {"inventory", "topology", "connections", "parameters"}
    ]
    uncertainty = [
        str(x).casefold() for x in (result.get("uncertainty_reasons") or [])
        if str(x).casefold() in _UNCERTAINTY
    ]
    comparability = _comparability(result.get("comparability"))
    status = str(result.get("status") or "unclear").casefold()
    if candidates:
        status = "changed_candidate"
    if status not in {"changed_candidate", "unchanged_candidate", "unclear"}:
        status = "unclear"
    if status == "unchanged_candidate" and (
        not result.get("room_visible_pd")
        or not result.get("room_visible_rd")
        or set(coverage) != {"inventory", "topology", "connections", "parameters"}
        or comparability != "high"
    ):
        status = "unclear"
    return {
        "room_id": normalize_room_key(room),
        "room_visible_pd": bool(result.get("room_visible_pd")),
        "room_visible_rd": bool(result.get("room_visible_rd")),
        "comparability": comparability,
        "engineering_elements_pd": result.get("engineering_elements_pd") or [],
        "engineering_elements_rd": result.get("engineering_elements_rd") or [],
        "connections_pd": result.get("connections_pd") or [],
        "connections_rd": result.get("connections_rd") or [],
        "candidate_differences": candidates,
        "uncertainty_reasons": uncertainty,
        "coverage": sorted(set(coverage)),
        "status": status,
        "summary": str(result.get("summary") or ""),
    }


def normalize_verification(result: dict, candidates, pass1_comparability: str = "low"):
    pass1_comp = _comparability(pass1_comparability)
    verifier_comp = _comparability(result.get("comparability"), pass1_comp)
    found = {}
    for item in result.get("verified") or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(candidates):
            continue
        verdict = str(item.get("verdict") or "unclear").casefold()
        if verdict not in {"confirmed", "rejected", "unclear"}:
            verdict = "unclear"
        # Fail closed: weak comparability cannot erase a high-recall candidate.
        if verdict == "rejected" and (
            pass1_comp != "high" or verifier_comp != "high"
        ):
            verdict = "unclear"
        found[idx] = {
            "idx": idx,
            "verdict": verdict,
            "reason": str(item.get("reason") or ""),
            "location": item.get("location"),
            "comparability": verifier_comp,
        }
    return [
        found.get(
            i,
            {
                "idx": i,
                "verdict": "unclear",
                "reason": "verification omitted candidate",
                "location": None,
                "comparability": verifier_comp,
            },
        )
        for i in range(len(candidates))
    ]
