"""Generic room-focused fallback for blind drawing comparison."""
from __future__ import annotations

import os
import re
from typing import Sequence

from .anchors import normalize_room_key


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_FOCUSED_ROOM_CALLS = _positive_int_env("NADZOR_MAX_FOCUSED_ROOM_VISION_CALLS", 12, 0, 40)
FOCUSED_ROOMS_PER_CALL = _positive_int_env("NADZOR_FOCUSED_ROOMS_PER_CALL", 6, 1, 8)
MAX_FOCUSED_ROOMS_PER_PAIR = _positive_int_env("NADZOR_MAX_FOCUSED_ROOMS_PER_PAIR", 12, 1, 32)

_ROOM_NUM_RE = re.compile(r"^(\d{1,4})(?:\.(\d+))?([а-яё]?)$", re.IGNORECASE)


def room_sort_key(room: str):
    key = normalize_room_key(room)
    match = _ROOM_NUM_RE.fullmatch(key)
    if not match:
        return (1, key)
    return (0, int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").casefold())


def select_rooms(shared_rooms: Sequence[str]) -> list[str]:
    rooms = {normalize_room_key(room) for room in shared_rooms if normalize_room_key(room)}
    return sorted(rooms, key=room_sort_key)[:MAX_FOCUSED_ROOMS_PER_PAIR]
