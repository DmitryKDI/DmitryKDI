"""Compatibility facade for the former triangulated analysis pipeline.

The previous implementation executed many independent branches in one run:
room/equipment registries, composition checks, requirement filtering, routing,
pair vision, triangulation and verdict synthesis. That orchestration is retired
from the active runtime because it duplicated work and made technical silence
look like semantic evidence.

The HTTP/API surface keeps the historical function name, but execution now goes
directly to :mod:`lean_analysis_runtime`:

    PD requirements -> RD evidence -> semantic compliance
    drawing pairs -> inventory -> region discovery -> local zoom -> evidence

Legacy modules remain in the repository for focused tests and possible offline
diagnostics; they are not called by this facade.
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
    """Compatibility entrypoint backed by the lean semantic runtime."""
    return run_lean_analysis(
        before_paths,
        after_paths,
        room_keys=room_keys,
        llm_config=llm_config,
        before_names=before_names,
        after_names=after_names,
    )


__all__ = ["DocumentLoadResult", "run_triangulated_analysis"]
