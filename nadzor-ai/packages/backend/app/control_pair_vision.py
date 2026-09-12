"""Compatibility facade for blind graphical controls.

The active runtime is intentionally small and evidence-first:

    pair discovery -> whole-page semantic compare -> model-driven zoom
    -> evidence contract -> result

Legacy room/non-room/raster orchestrators stay in the repository only for
compatibility and forensic comparison; they are not imported by this facade.
"""
from __future__ import annotations

from .semantic_pair_runtime import (
    MAX_GRAPHICAL_CANDIDATE_PAIRS,
    run_targeted_pair_vision,
)

__all__ = [
    "MAX_GRAPHICAL_CANDIDATE_PAIRS",
    "run_targeted_pair_vision",
]
