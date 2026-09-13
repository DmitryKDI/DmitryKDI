"""Compatibility facade for the active stateful analysis runtime.

The historical endpoint/function name remains stable, but active execution is:

    document map + PD requirements
        -> stateful GigaChat investigator
        -> Python search/open/zoom tools
        -> self-review
        -> independent verifier.

Legacy room/equipment/pair/routing/verdict modules remain available only for
regression tests and forensic comparison. They are not active semantic gates.
"""
from __future__ import annotations

from typing import Optional

from .lean_analysis_runtime import DocumentLoadResult, run_lean_analysis
from .llm import LlmConfig


def run_triangulated_analysis(
    before_paths: list[str],
    after_paths: list[str],
    room_keys: Optional[list[str]] = None,
    llm_config: Optional[LlmConfig] = None,
    before_names: Optional[list[str]] = None,
    after_names: Optional[list[str]] = None,
) -> dict:
    return run_lean_analysis(
        before_paths,
        after_paths,
        room_keys=room_keys,
        llm_config=llm_config,
        before_names=before_names,
        after_names=after_names,
    )


__all__ = ["DocumentLoadResult", "run_triangulated_analysis"]
