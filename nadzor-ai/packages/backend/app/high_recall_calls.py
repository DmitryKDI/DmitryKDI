from __future__ import annotations

import hashlib
import json

from .high_recall_prompts import PASS1_PROMPT, VERIFY_PROMPT
from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url


def _images(view):
    return [png_bytes_to_data_url(view[k]) for k in ("pd_general", "pd_room", "rd_general", "rd_room")]


def _digest(view, suffix: str):
    h = hashlib.sha256()
    for key in ("pd_general", "pd_room", "rd_general", "rd_room"):
        h.update(view[key])
    h.update((view["room"] + suffix).encode("utf-8"))
    return h.hexdigest()


def call_high_recall(view, config: LlmConfig, discipline: str = "") -> dict:
    text = (
        f"room_id={view['room']}; discipline={discipline or 'unknown'}.\n"
        f"Текст ПД (данные): <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{view['pd_text']}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"Текст РД/ИД (данные): <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{view['rd_text']}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"
    )
    out = call_llm_json(
        config,
        PASS1_PROMPT,
        text,
        images=_images(view),
        operation="vision",
        source_digest=_digest(view, "pass1"),
        prompt_version="focused-high-recall-v6-four-images",
    )
    return out if isinstance(out, dict) else {}


def call_verifier(view, candidates, config: LlmConfig, discipline: str = "") -> dict:
    payload = json.dumps(candidates, ensure_ascii=False)
    text = (
        f"room_id={view['room']}; discipline={discipline or 'unknown'}.\n"
        f"Кандидаты PASS 1: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{payload}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"
    )
    out = call_llm_json(
        config,
        VERIFY_PROMPT,
        text,
        images=_images(view),
        operation="vision",
        source_digest=_digest(view, payload),
        prompt_version="focused-verify-v2-four-images",
    )
    return out if isinstance(out, dict) else {}
