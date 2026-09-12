"""Compatibility facade for blind graphical controls."""
from __future__ import annotations

from .control_pair_candidates import ControlPair as _ControlPair
from .control_pair_candidates import candidate_pairs as _candidate_pairs
from .control_pair_runtime import (
    MAX_GRAPHICAL_CANDIDATE_PAIRS,
    run_room_entity_controls,
    run_targeted_pair_vision,
    semantic_scope_compare as _semantic_scope_compare,
    visual_change_evidence,
)
from .focused_pair_vision import MAX_FOCUSED_ROOM_CALLS, compare_shared_rooms_focused
from .matching import match_page_pairs

__all__ = [
    "MAX_GRAPHICAL_CANDIDATE_PAIRS",
    "MAX_FOCUSED_ROOM_CALLS",
    "run_targeted_pair_vision",
]
