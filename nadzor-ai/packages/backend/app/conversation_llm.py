"""Stateful textual conversation wrapper for the investigator runtime.

The provider adapter stays centralized in :mod:`app.llm`. This module preserves
investigation state by replaying a bounded transcript on every turn. Long
full-project runs keep the opening context plus the most recent turns instead
of growing the request without bound; the investigator's explicit SESSION NOTE
carries the durable notebook state.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Iterable

from .llm import LlmConfig, call_llm_json


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


HISTORY_MAX_CHARS = _int_env("NADZOR_INVESTIGATOR_HISTORY_CHARS", 70000, 20000, 180000)


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
    return _bounded_history(out, HISTORY_MAX_CHARS)


def _bounded_history(history: list[dict], max_chars: int) -> list[dict]:
    """Keep opening turn(s) and the newest complete turns within a char budget."""
    if max_chars <= 0:
        return []
    total = sum(len(str(item.get("content") or "")) for item in history)
    if total <= max_chars:
        return list(history)

    head = history[:2]
    head_chars = sum(len(str(item.get("content") or "")) for item in head)
    marker = {
        "role": "user",
        "content": (
            "[OLDER INVESTIGATION TURNS COMPACTED. The current SESSION NOTE and "
            "recent tool/model turns are authoritative for inspected refs, coverage, "
            "confirmed and pending candidates.]"
        ),
    }
    reserve = len(marker["content"]) + 200
    available = max(0, max_chars - head_chars - reserve)
    tail: list[dict] = []
    used = 0
    for item in reversed(history[2:]):
        size = len(str(item.get("content") or ""))
        if tail and used + size > available:
            break
        if not tail and size > available:
            clipped = dict(item)
            clipped["content"] = str(item.get("content") or "")[-available:] if available else ""
            if clipped["content"]:
                tail.append(clipped)
            break
        tail.append(item)
        used += size
    tail.reverse()
    return head + [marker] + tail


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
        prompt_version="stateful-investigator-conversation-v2",
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
