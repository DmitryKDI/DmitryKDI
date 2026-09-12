"""Canonical room-anchor normalization used across the blind runtime."""
from __future__ import annotations

import re
from collections.abc import Iterable

_ROOM_ID_RE = re.compile(r"(?<![\w])(?P<id>\d{1,4}(?:\.\d+)?[А-ЯA-Z]?)(?![\w])", re.IGNORECASE)
_ROOM_PREFIX_RE = re.compile(r"^\s*(?:пом(?:ещение)?|room)\.?\s*", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")


def normalize_room_key(value: object) -> str:
    text = _SPACE_RE.sub(" ", str(value or "").strip())
    if not text:
        return ""
    stripped = _ROOM_PREFIX_RE.sub("", text).strip(" ,;:()[]")
    matches = [m.group("id") for m in _ROOM_ID_RE.finditer(stripped)]
    if len(matches) == 1 and stripped.casefold() == matches[0].casefold():
        return matches[0]
    if len(matches) == 1 and not re.search(r"[А-ЯA-Z]{2,}", stripped, re.IGNORECASE):
        return matches[0]
    return stripped.casefold()


def normalize_room_references(values: Iterable[object] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = _SPACE_RE.sub(" ", str(raw or "").strip())
        if not text:
            continue
        ids = [m.group("id") for m in _ROOM_ID_RE.finditer(text)]
        candidates = ids if ids else [normalize_room_key(text)]
        for candidate in candidates:
            key = normalize_room_key(candidate)
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out
