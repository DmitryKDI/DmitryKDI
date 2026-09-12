"""Generic room-focused fallback for blind drawing comparison."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Sequence

import pymupdf

from .anchors import normalize_room_key
from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url
from .vision import render_page_to_png_bytes, vision_system_prompt
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


def grounded_rows(before_path: str, before_page: int, after_path: str, after_page: int, rooms: Sequence[str]):
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
            before_png = render_page_to_png_bytes(before_path, before_page, max_dim=820, clip_frac=before_crops[0])
            after_png = render_page_to_png_bytes(after_path, after_page, max_dim=820, clip_frac=after_crops[0])
        except Exception:
            missing.append(room)
            continue
        before_rows.append((room, before_png))
        after_rows.append((room, after_png))
        usable.append(room)
    return before_rows, after_rows, usable, missing


def call_focused(before_rows, after_rows, config: LlmConfig) -> dict:
    before_png = make_montage(before_rows)
    after_png = make_montage(after_rows)
    if not before_png or not after_png:
        return {}
    room_text = ", ".join(room for room, _ in before_rows)
    digest = hashlib.sha256(before_png + b"\0" + after_png + room_text.encode("utf-8")).hexdigest()
    user_text = (
        "Both images are aligned room montages. The first is PD and the second is RD/ID. "
        f"Compare only matching labels ROOM {room_text}. Focus on engineering systems and equipment. "
        "If a crop is only a schedule/table or is not readable, do not report a change for that room. "
        "In every significant item, mention the matching ROOM label in the change text."
    )
    result = call_llm_json(
        config,
        vision_system_prompt(None),
        user_text,
        images=[png_bytes_to_data_url(before_png), png_bytes_to_data_url(after_png)],
        operation="vision",
        source_digest=digest,
        prompt_version="focused-room-pair-v2",
    )
    return result if isinstance(result, dict) else {}


def compare_shared_rooms_focused(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_rooms: Sequence[str],
    config: LlmConfig,
    *,
    max_calls: int,
) -> tuple[list[dict], list[dict], int]:
    if max_calls <= 0 or not shared_rooms:
        return [], [], 0
    ordered = select_rooms(shared_rooms)
    findings: list[dict] = []
    diagnostics: list[dict] = []
    calls_used = 0
    for start in range(0, len(ordered), FOCUSED_ROOMS_PER_CALL):
        if calls_used >= max_calls:
            break
        requested = ordered[start:start + FOCUSED_ROOMS_PER_CALL]
        before_rows, after_rows, usable, missing = grounded_rows(
            before_path, before_page, after_path, after_page, requested
        )
        if not usable:
            diagnostics.append({"status": "focused_no_grounded_crops", "requested_rooms": requested, "missing_rooms": missing})
            continue
        calls_used += 1
        try:
            result = call_focused(before_rows, after_rows, config)
        except Exception as exc:
            diagnostics.append({"status": "focused_vision_error", "requested_rooms": requested, "usable_rooms": usable, "error": f"{type(exc).__name__}: {exc}"})
            continue
        raw_items = result.get("significant") if isinstance(result, dict) else []
        accepted: list[dict] = []
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict) or not str(item.get("change") or "").strip():
                continue
            normalized = dict(item)
            valid_rooms: list[str] = []
            for raw in item.get("rooms") or []:
                room = normalize_room_key(raw)
                if room in usable and room not in valid_rooms:
                    valid_rooms.append(room)
            normalized["rooms"] = valid_rooms
            accepted.append(normalized)
            findings.append(normalized)
        diagnostics.append({
            "status": "focused_significant" if accepted else "focused_no_change",
            "requested_rooms": requested,
            "usable_rooms": usable,
            "missing_rooms": missing,
            "findings": accepted,
            "noise_note": str(result.get("noise_note") or ""),
        })
    return findings, diagnostics, calls_used
