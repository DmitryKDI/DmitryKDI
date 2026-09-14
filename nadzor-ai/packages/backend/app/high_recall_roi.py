from __future__ import annotations

import math
import os
import re
from typing import Sequence

import pymupdf

from .anchors import normalize_room_key
from .vision import render_page_to_png_bytes
from .vision_page_compare import room_label_crops


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_FOCUSED_ROOMS_PER_PAIR = _int_env("NADZOR_MAX_FOCUSED_ROOMS_PER_PAIR", 20, 1, 60)
FOCUSED_CROP_PADDING = _float_env("NADZOR_FOCUSED_CROP_PADDING", 0.28, 0.10, 0.50)
FOCUSED_GENERAL_MAX_DIM = _int_env("NADZOR_FOCUSED_GENERAL_MAX_DIM", 2500, 1400, 2800)
FOCUSED_ROOM_MAX_DIM = _int_env("NADZOR_FOCUSED_ROOM_MAX_DIM", 2600, 1600, 2800)
_ROOM_RE = re.compile(r"^(\d{1,4})(?:\.(\d+))?([а-яё]?)$", re.IGNORECASE)


def room_sort_key(room: str):
    key = normalize_room_key(room)
    match = _ROOM_RE.fullmatch(key)
    if not match:
        return (1, key)
    return (0, int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").casefold())


def select_rooms(shared_rooms: Sequence[str]) -> list[str]:
    rooms = sorted({normalize_room_key(x) for x in shared_rooms if normalize_room_key(x)}, key=room_sort_key)
    if len(rooms) <= MAX_FOCUSED_ROOMS_PER_PAIR:
        return rooms
    n = MAX_FOCUSED_ROOMS_PER_PAIR
    if n == 1:
        return [rooms[len(rooms) // 2]]
    indexes = {int(round(i * (len(rooms) - 1) / (n - 1))) for i in range(n)}
    return [rooms[i] for i in sorted(indexes)]


def expand_clip(clip, pad_ratio: float = FOCUSED_CROP_PADDING):
    x0, y0, x1, y1 = clip
    w, h = max(0.001, x1 - x0), max(0.001, y1 - y0)
    return (max(0.0, x0 - w * pad_ratio), max(0.0, y0 - h * pad_ratio), min(1.0, x1 + w * pad_ratio), min(1.0, y1 + h * pad_ratio))


def _rect_gap(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return math.hypot(dx, dy)


def geometry_context_crop(pdf_path: str, page_no: int, label_clip):
    """Grow label anchor toward nearby vector geometry and boundary connections."""
    fallback = expand_clip(tuple(label_clip))
    try:
        doc = pymupdf.open(pdf_path)
        try:
            page = doc[page_no - 1]
            pr = page.rect
            current = pymupdf.Rect(fallback[0] * pr.width, fallback[1] * pr.height, fallback[2] * pr.width, fallback[3] * pr.height)
            max_area = pr.width * pr.height * 0.46
            proximity = max(pr.width, pr.height) * 0.012
            rects = [pymupdf.Rect(d["rect"]) for d in page.get_drawings() if d.get("rect")]
            for _ in range(2):
                changed = False
                for rect in sorted(rects, key=lambda r: _rect_gap(current, r))[:100]:
                    if _rect_gap(current, rect) > proximity:
                        continue
                    merged = current | rect
                    if merged.width * merged.height <= max_area and merged != current:
                        current = merged
                        changed = True
                if not changed:
                    break
            frac = (current.x0 / pr.width, current.y0 / pr.height, current.x1 / pr.width, current.y1 / pr.height)
            return expand_clip(frac, 0.12)
        finally:
            doc.close()
    except Exception:
        return fallback


def _gray(pixel) -> float:
    return float(pixel[0]) if len(pixel) == 1 else sum(float(x) for x in pixel[:3]) / min(3, len(pixel))


def _grid(png: bytes, n: int = 14):
    pix = pymupdf.Pixmap(png)
    return [[255.0 - _gray(pix.pixel(min(pix.width - 1, int((x + .5) / n * pix.width)), min(pix.height - 1, int((y + .5) / n * pix.height)))) for x in range(n)] for y in range(n)]


def aligned_grid_difference(a_png: bytes, b_png: bytes) -> float:
    a, b = _grid(a_png), _grid(b_png)
    n, center, best = len(a), (len(a) - 1) / 2, math.inf
    for scale in (.90, 1.0, 1.10):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                total = count = 0
                for y in range(n):
                    by = int(round(center + (y - center) * scale + dy))
                    if not 0 <= by < n:
                        continue
                    for x in range(n):
                        bx = int(round(center + (x - center) * scale + dx))
                        if 0 <= bx < n:
                            total += abs(a[y][x] - b[by][bx]); count += 1
                if count:
                    best = min(best, total / count / 255.0)
    return 0.0 if not math.isfinite(best) else float(best)


def room_candidate(before_path: str, before_page: int, after_path: str, after_page: int, room: str):
    """Return priority score and geometry-aware clips. Score never gates Vision."""
    try:
        before = room_label_crops(before_path, before_page, room, max_crops=3)
        after = room_label_crops(after_path, after_page, room, max_crops=3)
        if not before or not after:
            return None, None, None
        best = None
        for b in before:
            for a in after:
                bc = geometry_context_crop(before_path, before_page, b)
                ac = geometry_context_crop(after_path, after_page, a)
                bp = render_page_to_png_bytes(before_path, before_page, max_dim=420, clip_frac=bc)
                ap = render_page_to_png_bytes(after_path, after_page, max_dim=420, clip_frac=ac)
                score = aligned_grid_difference(bp, ap) - .02 * (bc[1] > .82 or ac[1] > .82)
                if best is None or score > best[0]:
                    best = (score, bc, ac)
        return best
    except Exception:
        return None, None, None


def prioritize_rooms(before_path: str, before_page: int, after_path: str, after_page: int, shared_rooms: Sequence[str]):
    rows, diag = [], []
    for room in select_rooms(shared_rooms):
        score, bc, ac = room_candidate(before_path, before_page, after_path, after_page, room)
        rows.append((-(score if score is not None else -1.0), room_sort_key(room), room))
        diag.append({"room": room, "local_diff_score": None if score is None else round(score, 6), "before_clip": bc, "after_clip": ac})
    rows.sort(); ordered = [x[2] for x in rows]; rank = {r: i + 1 for i, r in enumerate(ordered)}
    for item in diag:
        item["priority_rank"] = rank[item["room"]]
    return ordered, sorted(diag, key=lambda x: x["priority_rank"])


def page_text(path: str, page_no: int) -> str:
    try:
        doc = pymupdf.open(path)
        try:
            return (doc[page_no - 1].get_text("text") or "")[:2200]
        finally:
            doc.close()
    except Exception:
        return ""


def grounded_view(before_path: str, before_page: int, after_path: str, after_page: int, room: str):
    score, bc, ac = room_candidate(before_path, before_page, after_path, after_page, room)
    if bc is None or ac is None:
        return None
    try:
        return {"room": normalize_room_key(room), "pd_general": render_page_to_png_bytes(before_path, before_page, max_dim=FOCUSED_GENERAL_MAX_DIM), "pd_room": render_page_to_png_bytes(before_path, before_page, max_dim=FOCUSED_ROOM_MAX_DIM, clip_frac=bc), "rd_general": render_page_to_png_bytes(after_path, after_page, max_dim=FOCUSED_GENERAL_MAX_DIM), "rd_room": render_page_to_png_bytes(after_path, after_page, max_dim=FOCUSED_ROOM_MAX_DIM, clip_frac=ac), "pd_clip": bc, "rd_clip": ac, "local_diff_score": score, "pd_text": page_text(before_path, before_page), "rd_text": page_text(after_path, after_page)}
    except Exception:
        return None
