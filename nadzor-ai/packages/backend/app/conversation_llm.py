"""Stateful textual conversation wrapper for the investigator runtime.

The provider adapter stays centralized in :mod:`app.llm`. This module preserves
investigation state by replaying a compact transcript on every turn. That makes
the model see its prior observations and decisions instead of starting each
micro-call from zero, while keeping authentication/network logic in one place.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable

from .llm import LlmConfig, call_llm_json


def _clean_history(history: Iterable[dict] | None) -> list[dict]:
    out: list[dict] = []
    for item in history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        out.append({"role": role, "content": content})
    return out


def _history_text(history: list[dict]) -> str:
    if not history:
        return ""
    lines = ["<INVESTIGATION_HISTORY>"]
    for item in history:
        role = "INSPECTOR" if item["role"] == "assistant" else "TOOL_OR_USER"
        lines.append(f"[{role}]\n{item['content']}")
    lines.append("</INVESTIGATION_HISTORY>")
    return "\n".join(lines)


def call_conversation_json(
    config: LlmConfig,
    system_prompt: str,
    history: list[dict],
    user_text: str,
    *,
    images: list[str] | None = None,
    timeout: float = 180.0,
    operation: str = "vision",
) -> dict:
    clean = _clean_history(history)
    conversation = _history_text(clean)
    combined = (
        (conversation + "\n\n" if conversation else "")
        + "<CURRENT_TURN>\n"
        + user_text
        + "\n</CURRENT_TURN>"
    )
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    result = call_llm_json(
        config,
        system_prompt,
        combined,
        images=list(images or []),
        timeout=timeout,
        operation=operation,
        source_digest=digest,
        prompt_version="stateful-investigator-conversation-v1",
        use_cache=False,
    )
    return result if isinstance(result, dict) else {}


def append_turn(history: list[dict], user_text: str, assistant_json: dict) -> None:
    history.append({"role": "user", "content": user_text})
    history.append(
        {
            "role": "assistant",
            "content": json.dumps(assistant_json, ensure_ascii=False, separators=(",", ":")),
        }
    )
