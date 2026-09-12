"""Compatibility facade for the high-recall blind room vision pipeline."""
from __future__ import annotations

from .high_recall_calls import call_high_recall, call_verifier
from .high_recall_normalize import normalize_high_recall, normalize_verification
from .high_recall_orchestrator import (
    FOCUSED_ROOMS_PER_CALL,
    MAX_FOCUSED_CALLS_PER_PAIR,
    MAX_FOCUSED_ROOM_CALLS,
    compare_shared_rooms_focused,
)
from .high_recall_roi import (
    FOCUSED_CROP_PADDING,
    FOCUSED_GENERAL_MAX_DIM,
    FOCUSED_ROOM_MAX_DIM,
    MAX_FOCUSED_ROOMS_PER_PAIR,
    aligned_grid_difference as _aligned_grid_difference,
    expand_clip as _expand_clip,
    geometry_context_crop as _geometry_context_crop,
    grounded_view,
    prioritize_rooms,
    room_candidate as _render_room_pair_for_score,
    room_sort_key,
    select_rooms,
)

__all__ = [
    "MAX_FOCUSED_ROOM_CALLS",
    "MAX_FOCUSED_CALLS_PER_PAIR",
    "FOCUSED_ROOMS_PER_CALL",
    "MAX_FOCUSED_ROOMS_PER_PAIR",
    "FOCUSED_CROP_PADDING",
    "FOCUSED_GENERAL_MAX_DIM",
    "FOCUSED_ROOM_MAX_DIM",
    "select_rooms",
    "room_sort_key",
    "prioritize_rooms",
    "grounded_view",
    "call_high_recall",
    "call_verifier",
    "normalize_high_recall",
    "normalize_verification",
    "compare_shared_rooms_focused",
]
