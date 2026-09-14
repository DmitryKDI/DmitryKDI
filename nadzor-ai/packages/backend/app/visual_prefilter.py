"""Cheap structural raster diff used before semantic vision.

Large engineering sheets are often exported with different crop, translation or
small scale drift. Direct cell-to-cell comparison then reports half the sheet as
changed and hides local engineering edits. This implementation first aligns a
coarse darkness grid by small translation/scale search, then computes changed
cells in the aligned coordinate system. The result remains only candidate
evidence; it never decides compliance by itself.
"""
from __future__ import annotations

import math
from typing import Optional

import pymupdf

_GRID = 24
_SAMPLES = 4
_CELL_DARKNESS_THRESHOLD = 14.0
DIFF_RATIO_THRESHOLD = 0.015
_LOCAL_MIN_CHANGED_CELLS = 3
_LOCAL_MAX_AREA = 0.35
_HOT_ZONE_PADDING = 0.08
_HOT_ZONE_MAX_FRACTION = 0.80
_ALIGNMENT_SCALES = (0.90, 0.95, 1.0, 1.05, 1.10)
_ALIGNMENT_SHIFTS = range(-3, 4)
_ALIGNMENT_MARGIN = 2
_COMPARE_MARGIN = 1


def _render_gray(pdf_path: str, page_no: int, max_dim: int = 520):
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        rect = page.rect
        scale = max_dim / max(rect.width, rect.height)
        scale = min(scale, 4.0)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY)
    finally:
        doc.close()


def _mean_darkness(pix, cell_x: int, cell_y: int) -> float:
    values: list[float] = []
    for sx in range(_SAMPLES):
        for sy in range(_SAMPLES):
            fx = (cell_x + (sx + 0.5) / _SAMPLES) / _GRID
            fy = (cell_y + (sy + 0.5) / _SAMPLES) / _GRID
            x = min(max(int(fx * pix.width), 0), pix.width - 1)
            y = min(max(int(fy * pix.height), 0), pix.height - 1)
            gray = pix.pixel(x, y)[0]
            values.append(255.0 - float(gray))
    return sum(values) / len(values)


def _darkness_grid(pix) -> list[list[float]]:
    return [
        [_mean_darkness(pix, x, y) for x in range(_GRID)]
        for y in range(_GRID)
    ]


def _mapped_index(index: int, scale: float, shift: int) -> int:
    center = (_GRID - 1) / 2.0
    return int(round(center + (index - center) * scale + shift))


def _alignment_score(before, after, scale: float, dx: int, dy: int) -> tuple[float, int]:
    total = 0.0
    count = 0
    for y in range(_ALIGNMENT_MARGIN, _GRID - _ALIGNMENT_MARGIN):
        ay = _mapped_index(y, scale, dy)
        if ay < _ALIGNMENT_MARGIN or ay >= _GRID - _ALIGNMENT_MARGIN:
            continue
        for x in range(_ALIGNMENT_MARGIN, _GRID - _ALIGNMENT_MARGIN):
            ax = _mapped_index(x, scale, dx)
            if ax < _ALIGNMENT_MARGIN or ax >= _GRID - _ALIGNMENT_MARGIN:
                continue
            total += abs(before[y][x] - after[ay][ax])
            count += 1
    if not count:
        return math.inf, 0
    return total / count, count


def _best_alignment(before, after) -> dict:
    best = {
        "scale": 1.0,
        "dx_cells": 0,
        "dy_cells": 0,
        "mean_delta": math.inf,
        "samples": 0,
    }
    for scale in _ALIGNMENT_SCALES:
        for dx in _ALIGNMENT_SHIFTS:
            for dy in _ALIGNMENT_SHIFTS:
                score, count = _alignment_score(before, after, scale, dx, dy)
                if score < best["mean_delta"]:
                    best = {
                        "scale": scale,
                        "dx_cells": dx,
                        "dy_cells": dy,
                        "mean_delta": score,
                        "samples": count,
                    }
    max_samples = (_GRID - 2 * _ALIGNMENT_MARGIN) ** 2
    best["overlap_ratio"] = best["samples"] / float(max_samples)
    best["mean_delta"] = round(float(best["mean_delta"]), 4)
    best["scale"] = round(float(best["scale"]), 3)
    best["overlap_ratio"] = round(float(best["overlap_ratio"]), 4)
    return best


def _raw_diff_cells(before, after) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for y in range(_GRID):
        for x in range(_GRID):
            if abs(before[y][x] - after[y][x]) >= _CELL_DARKNESS_THRESHOLD:
                cells.add((x, y))
    return cells


def _aligned_diff_cells(before, after, alignment: dict) -> tuple[set[tuple[int, int]], int]:
    scale = float(alignment["scale"])
    dx = int(alignment["dx_cells"])
    dy = int(alignment["dy_cells"])
    cells: set[tuple[int, int]] = set()
    compared = 0
    for y in range(_COMPARE_MARGIN, _GRID - _COMPARE_MARGIN):
        ay = _mapped_index(y, scale, dy)
        if ay < _COMPARE_MARGIN or ay >= _GRID - _COMPARE_MARGIN:
            continue
        for x in range(_COMPARE_MARGIN, _GRID - _COMPARE_MARGIN):
            ax = _mapped_index(x, scale, dx)
            if ax < _COMPARE_MARGIN or ax >= _GRID - _COMPARE_MARGIN:
                continue
            compared += 1
            if abs(before[y][x] - after[ay][ax]) >= _CELL_DARKNESS_THRESHOLD:
                cells.add((x, y))
    return cells, compared


def _diff_evidence(before_path: str, before_page: int, after_path: str, after_page: int):
    before_grid = _darkness_grid(_render_gray(before_path, before_page))
    after_grid = _darkness_grid(_render_gray(after_path, after_page))
    raw_cells = _raw_diff_cells(before_grid, after_grid)
    alignment = _best_alignment(before_grid, after_grid)
    cells, compared = _aligned_diff_cells(before_grid, after_grid, alignment)
    return raw_cells, cells, compared, alignment


def visual_diff_ratio(before_path: str, before_page: int, after_path: str, after_page: int) -> float:
    _, cells, compared, _ = _diff_evidence(before_path, before_page, after_path, after_page)
    return len(cells) / float(max(1, compared))


def _hot_zone_from_cells(cells: set[tuple[int, int]]) -> Optional[tuple[float, float, float, float]]:
    if not cells:
        return None
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    x0, x1 = min(xs) / _GRID, (max(xs) + 1) / _GRID
    y0, y1 = min(ys) / _GRID, (max(ys) + 1) / _GRID
    if (x1 - x0) > _HOT_ZONE_MAX_FRACTION or (y1 - y0) > _HOT_ZONE_MAX_FRACTION:
        return None
    return (
        max(0.0, x0 - _HOT_ZONE_PADDING),
        max(0.0, y0 - _HOT_ZONE_PADDING),
        min(1.0, x1 + _HOT_ZONE_PADDING),
        min(1.0, y1 + _HOT_ZONE_PADDING),
    )


def diff_hot_zone(before_path: str, before_page: int, after_path: str, after_page: int):
    _, cells, _, _ = _diff_evidence(before_path, before_page, after_path, after_page)
    return _hot_zone_from_cells(cells)


def visual_change_evidence(before_path: str, before_page: int, after_path: str, after_page: int) -> dict:
    """Return registration-aware deterministic evidence for semantic vision."""
    raw_cells, cells, compared, alignment = _diff_evidence(
        before_path, before_page, after_path, after_page
    )
    raw_ratio = len(raw_cells) / float(_GRID * _GRID)
    ratio = len(cells) / float(max(1, compared))
    zone = _hot_zone_from_cells(cells)
    zone_area = None
    if zone is not None:
        zone_area = (zone[2] - zone[0]) * (zone[3] - zone[1])
    local_cluster = (
        len(cells) >= _LOCAL_MIN_CHANGED_CELLS
        and zone is not None
        and zone_area is not None
        and zone_area <= _LOCAL_MAX_AREA
    )
    return {
        "significant": bool(ratio >= DIFF_RATIO_THRESHOLD or local_cluster),
        "raw_diff_ratio": round(raw_ratio, 6),
        "diff_ratio": round(ratio, 6),
        "changed_cells": len(cells),
        "compared_cells": compared,
        "hot_zone": zone,
        "local_cluster": local_cluster,
        "alignment": alignment,
    }


def is_visually_different(before_path: str, before_page: int, after_path: str, after_page: int) -> bool:
    try:
        return bool(visual_change_evidence(before_path, before_page, after_path, after_page)["significant"])
    except Exception:  # noqa: BLE001
        return False
