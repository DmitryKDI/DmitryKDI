"""Compatibility facade for blind graphical controls.

The active comparison path is deliberately simple:
pair discovery -> whole-page semantic compare -> model-driven zoom.
Specialized room/non-room helpers remain available in their modules but are no
longer the default orchestration path.
"""
from __future__ import annotations

from .control_pair_candidates import ControlPair as _ControlPair
from .control_pair_candidates import candidate_pairs as _candidate_pairs
from .control_pair_runtime import (
    MAX_GRAPHICAL_CANDIDATE_PAIRS,
    run_room_entity_controls,
    semantic_scope_compare as _semantic_scope_compare,
    visual_change_evidence,
)
from .focused_pair_vision import MAX_FOCUSED_ROOM_CALLS, compare_shared_rooms_focused
from .matching import match_page_pairs
from .simple_pair_runtime import run_targeted_pair_vision

__all__ = [
    "MAX_GRAPHICAL_CANDIDATE_PAIRS",
    "MAX_FOCUSED_ROOM_CALLS",
    "run_targeted_pair_vision",
]
