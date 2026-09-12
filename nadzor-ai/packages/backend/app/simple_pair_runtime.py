"""Backward-compatible import shim for the clean semantic pair runtime.

Kept only so older imports/tests do not break. The implementation lives in
`semantic_pair_runtime.py`; no legacy room/non-room/raster orchestration runs
through this module anymore.
"""
from __future__ import annotations

from .semantic_pair_runtime import (
    MAX_GRAPHICAL_CANDIDATE_PAIRS,
    MAX_REGION_ZOOMS_PER_PAIR,
    run_targeted_pair_vision,
)

__all__ = [
    "MAX_GRAPHICAL_CANDIDATE_PAIRS",
    "MAX_REGION_ZOOMS_PER_PAIR",
    "run_targeted_pair_vision",
]
