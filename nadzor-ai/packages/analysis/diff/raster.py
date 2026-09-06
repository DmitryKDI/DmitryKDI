"""Растровое сравнение листов PDF.

Листы приводятся к серому изображению и сравниваются по сетке ячеек.
Отличающиеся ячейки объединяются в области — их подсвечивает интерфейс
в режиме «было / стало».
"""
from __future__ import annotations

import pymupdf

from analysis.diff.ports import DiffRegion

GRID_COLS = 48
GRID_ROWS = 66
SAMPLE_STEP = 2          # шаг прореживания пикселей
CELL_THRESHOLD = 12      # порог средней разницы яркости в ячейке


class RasterDiffEngine:
    """Сравнение растровых представлений листов."""

    name = "raster"
    supported_formats = frozenset({"pdf", "png", "jpeg"})

    def __init__(self, dpi: int = 72) -> None:
        self.dpi = dpi

    def compare(self, before: bytes, after: bytes) -> list[DiffRegion]:
        doc_a = pymupdf.open(stream=before, filetype="pdf")
        doc_b = pymupdf.open(stream=after, filetype="pdf")
        regions: list[DiffRegion] = []
        for index in range(max(doc_a.page_count, doc_b.page_count)):
            page_a = doc_a[index] if index < doc_a.page_count else None
            page_b = doc_b[index] if index < doc_b.page_count else None
            regions.extend(self._compare_pages(page_a, page_b, index + 1))
        doc_a.close()
        doc_b.close()
        return regions

    def _compare_pages(self, page_a, page_b, page_no: int) -> list[DiffRegion]:
        if page_a is None or page_b is None:
            present = page_b if page_a is None else page_a
            rect = present.rect
            return [DiffRegion(page=page_no, kind="added" if page_a is None else "removed",
                               bbox_before=None if page_a is None else tuple(rect),
                               bbox_after=None if page_b is None else tuple(rect),
                               payload="лист присутствует только в одном состоянии", weight=1.0)]
        grid_a, size_a = self._grid(page_a)
        grid_b, size_b = self._grid(page_b)
        width, height = size_b
        cell_w, cell_h = width / GRID_COLS, height / GRID_ROWS
        changed = [(r, c) for r in range(GRID_ROWS) for c in range(GRID_COLS)
                   if abs(grid_a[r][c] - grid_b[r][c]) > CELL_THRESHOLD]
        return [self._region(box, page_no, cell_w, cell_h, width, height)
                for box in _merge(changed)]

    @staticmethod
    def _region(box, page_no: int, cell_w: float, cell_h: float,
                width: float, height: float) -> DiffRegion:
        r0, c0, r1, c1 = box
        bbox = (c0 * cell_w, r0 * cell_h, (c1 + 1) * cell_w, (r1 + 1) * cell_h)
        area = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (width * height)
        return DiffRegion(page=page_no, kind="changed", bbox_before=bbox, bbox_after=bbox,
                          payload="изменение изображения листа", weight=round(area, 4))

    def _grid(self, page) -> tuple[list[list[float]], tuple[float, float]]:
        """Средняя яркость по ячейкам сетки."""
        pix = page.get_pixmap(dpi=self.dpi, colorspace=pymupdf.csGRAY)
        data, w, h = pix.samples, pix.width, pix.height
        grid = [[0.0] * GRID_COLS for _ in range(GRID_ROWS)]
        counts = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
        for y in range(0, h, SAMPLE_STEP):
            row = (y * GRID_ROWS) // h
            base = y * w
            for x in range(0, w, SAMPLE_STEP):
                col = (x * GRID_COLS) // w
                grid[row][col] += data[base + x]
                counts[row][col] += 1
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                if counts[r][c]:
                    grid[r][c] /= counts[r][c]
        return grid, (float(page.rect.width), float(page.rect.height))


def _merge(cells: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Объединить соседние изменённые ячейки в прямоугольные области."""
    remaining = set(cells)
    boxes = []
    while remaining:
        seed = remaining.pop()
        stack, group = [seed], [seed]
        while stack:
            r, c = stack.pop()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
                neighbour = (r + dr, c + dc)
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    stack.append(neighbour)
                    group.append(neighbour)
        rows = [r for r, _ in group]
        cols = [c for _, c in group]
        boxes.append((min(rows), min(cols), max(rows), max(cols)))
    return sorted(boxes, key=lambda b: (b[0], b[1]))
