"""Cheap structural raster diff used before semantic vision.

The previous implementation sampled one pixel in each 16x16 cell.  Thin
engineering lines could pass between those points and a real local change was
reported as identical.  This version compares mean darkness from several
samples per cell and treats compact local clusters as significant even when
they occupy only a small fraction of a large sheet.
"""
from __future__ import annotations

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


def _diff_cells(before_path: str, before_page: int, after_path: str, after_page: int) -> set[tuple[int, int]]:
    before = _render_gray(before_path, before_page)
    after = _render_gray(after_path, after_page)
    cells: set[tuple[int, int]] = set()
    for x in range(_GRID):
        for y in range(_GRID):
            delta = abs(_mean_darkness(before, x, y) - _mean_darkness(after, x, y))
            if delta >= _CELL_DARKNESS_THRESHOLD:
                cells.add((x, y))
    return cells


def visual_diff_ratio(before_path: str, before_page: int, after_path: str, after_page: int) -> float:
    return len(_diff_cells(before_path, before_page, after_path, after_page)) / float(_GRID * _GRID)


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
    return _hot_zone_from_cells(_diff_cells(before_path, before_page, after_path, after_page))


def visual_change_evidence(before_path: str, before_page: int, after_path: str, after_page: int) -> dict:
    """Return deterministic evidence for whether semantic vision is warranted."""
    cells = _diff_cells(before_path, before_page, after_path, after_page)
    ratio = len(cells) / float(_GRID * _GRID)
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
        "diff_ratio": round(ratio, 6),
        "changed_cells": len(cells),
        "hot_zone": zone,
        "local_cluster": local_cluster,
    }


def is_visually_different(before_path: str, before_page: int, after_path: str, after_page: int) -> bool:
    try:
        return bool(visual_change_evidence(before_path, before_page, after_path, after_page)["significant"])
    except Exception:  # noqa: BLE001
        return False
