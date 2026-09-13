from __future__ import annotations

import json
import os
from typing import Sequence

from .high_recall_calls import call_high_recall, call_verifier
from .high_recall_normalize import normalize_high_recall, normalize_verification
from .high_recall_roi import FOCUSED_GENERAL_MAX_DIM, FOCUSED_ROOM_MAX_DIM, grounded_view, prioritize_rooms
from .llm import LlmConfig


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_FOCUSED_ROOM_CALLS = _int_env("NADZOR_MAX_FOCUSED_ROOM_VISION_CALLS", 36, 0, 96)
MAX_FOCUSED_CALLS_PER_PAIR = _int_env("NADZOR_MAX_FOCUSED_CALLS_PER_PAIR", 6, 1, 12)
FOCUSED_ROOMS_PER_CALL = 1


def compare_shared_rooms_focused(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_rooms: Sequence[str],
    config: LlmConfig,
    *,
    max_calls: int,
    discipline: str = "",
):
    if max_calls <= 0 or not shared_rooms:
        return [], [], 0

    ordered, priority = prioritize_rooms(before_path, before_page, after_path, after_page, shared_rooms)
    findings = []
    diagnostics = [{"status": "focused_room_priority", "room_priority": priority}]
    used = 0
    budget = min(max_calls, MAX_FOCUSED_CALLS_PER_PAIR)

    for room in ordered:
        if used >= budget:
            break
        view = grounded_view(before_path, before_page, after_path, after_page, room)
        if view is None:
            diagnostics.append({"status": "focused_no_grounded_crops", "requested_rooms": [room]})
            continue

        used += 1
        try:
            p1 = normalize_high_recall(call_high_recall(view, config, discipline), room)
        except Exception as exc:
            diagnostics.append({
                "status": "focused_vision_error",
                "execution_state": "failed",
                "requested_rooms": [room],
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        candidates = p1["candidate_differences"]
        verification = []
        if candidates:
            if used < budget:
                used += 1
                try:
                    raw_verification = call_verifier(
                        view,
                        candidates,
                        config,
                        discipline,
                        pass1_comparability=p1["comparability"],
                    )
                    verification = normalize_verification(
                        raw_verification,
                        candidates,
                        p1["comparability"],
                    )
                except Exception:
                    verification = normalize_verification(
                        {}, candidates, p1["comparability"]
                    )
            else:
                verification = normalize_verification(
                    {}, candidates, p1["comparability"]
                )

        accepted = []
        for idx, cand in enumerate(candidates):
            verdict = verification[idx]["verdict"] if idx < len(verification) else "unclear"
            if verdict == "rejected":
                continue
            finding = {
                "label": "Инженерное визуальное различие" if verdict == "confirmed" else "Кандидат инженерного визуального различия",
                "change": cand["description"],
                "rooms": [p1["room_id"]],
                "pd_observation": json.dumps(p1["engineering_elements_pd"], ensure_ascii=False),
                "rd_observation": json.dumps(p1["engineering_elements_rd"], ensure_ascii=False),
                "differences": [cand],
                "comparability": p1["comparability"],
                "verification": verdict,
                "verification_reason": verification[idx]["reason"] if idx < len(verification) else "",
            }
            findings.append(finding)
            accepted.append(finding)

        status = "focused_significant" if accepted else (
            "focused_unclear" if p1["status"] == "unclear" else "focused_complete_no_change"
        )
        diagnostics.append({
            "status": status,
            "execution_state": "completed",
            "requested_rooms": [room],
            "comparability": p1["comparability"],
            "selected_clips": {room: {
                "before_clip": view["pd_clip"],
                "after_clip": view["rd_clip"],
                "local_diff_score": view["local_diff_score"],
                "image_layout": "D: pd_general,pd_room,rd_general,rd_room",
                "general_max_dim": FOCUSED_GENERAL_MAX_DIM,
                "room_max_dim": FOCUSED_ROOM_MAX_DIM,
            }},
            "pass1": p1,
            "verification": verification,
            "findings": accepted,
            "pair_call": used,
            "pair_call_budget": budget,
        })

    if used >= budget and ordered:
        diagnostics.append({
            "status": "focused_pair_budget",
            "rooms_total": len(ordered),
            "calls_used": used,
            "pair_call_budget": budget,
        })
    return findings, diagnostics, used
