"""Generic room-focused fallback for blind drawing comparison."""
from __future__ import annotations

import os
import re
from typing import Sequence

import pymupdf

from .anchors import normalize_room_key
from .vision import render_page_to_png_bytes
from .vision_page_compare import room_label_crops


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


def _fit_rect(width: float, height: float, box: pymupdf.Rect) -> pymupdf.Rect:
    scale = min(box.width / max(width, 1.0), box.height / max(height, 1.0))
    target_w = width * scale
    target_h = height * scale
    x0 = box.x0 + (box.width - target_w) / 2
    y0 = box.y0 + (box.height - target_h) / 2
    return pymupdf.Rect(x0, y0, x0 + target_w, y0 + target_h)


def make_montage(rows: Sequence[tuple[str, bytes]]) -> bytes:
    """Combine labelled room crops into one bounded image."""
    if not rows:
        return b""
    width = 900.0
    row_height = 330.0
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width, height=row_height * len(rows))
        for index, (room, png) in enumerate(rows):
            y = index * row_height
            page.insert_text((16, y + 22), f"ROOM {room}", fontsize=13)
            pix = pymupdf.Pixmap(png)
            box = pymupdf.Rect(16, y + 32, width - 16, y + row_height - 10)
            page.insert_image(_fit_rect(float(pix.width), float(pix.height), box), stream=png)
        return page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False).tobytes("png")
    finally:
        doc.close()


def grounded_rows(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    rooms: Sequence[str],
):
    """Render the first grounded crop for each room on both sides."""
    before_rows: list[tuple[str, bytes]] = []
    after_rows: list[tuple[str, bytes]] = []
    usable: list[str] = []
    missing: list[str] = []
    for room in rooms:
        before_crops = room_label_crops(before_path, before_page, room, max_crops=3)
        after_crops = room_label_crops(after_path, after_page, room, max_crops=3)
        if not before_crops or not after_crops:
            missing.append(room)
            continue
        try:
            before_png = render_page_to_png_bytes(
                before_path, before_page, max_dim=820, clip_frac=before_crops[0]
            )
            after_png = render_page_to_png_bytes(
                after_path, after_page, max_dim=820, clip_frac=after_crops[0]
            )
        except Exception:  # noqa: BLE001
            missing.append(room)
            continue
        before_rows.append((room, before_png))
        after_rows.append((room, after_png))
        usable.append(room)
    return before_rows, after_rows, usable, missing
