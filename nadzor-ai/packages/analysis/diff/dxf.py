"""Сравнение чертежей в формате DXF по составу примитивов."""
from __future__ import annotations

import io

from analysis.diff.ports import DiffRegion


class DxfDiffEngine:
    """Сравнение DXF: примитивы сопоставляются по слою, типу и геометрии."""

    name = "dxf"
    supported_formats = frozenset({"dxf"})

    def __init__(self, precision: int = 1) -> None:
        self.precision = precision

    def compare(self, before: bytes, after: bytes) -> list[DiffRegion]:
        old = self._signatures(before)
        new = self._signatures(after)
        regions: list[DiffRegion] = []
        for key, bbox in new.items():
            if key not in old:
                regions.append(DiffRegion(page=1, kind="added", bbox_before=None,
                                          bbox_after=bbox, payload=key))
        for key, bbox in old.items():
            if key not in new:
                regions.append(DiffRegion(page=1, kind="removed", bbox_before=bbox,
                                          bbox_after=None, payload=key))
        return regions

    def _signatures(self, data: bytes) -> dict[str, tuple[float, float, float, float]]:
        import ezdxf

        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="ignore")))
        out: dict[str, tuple[float, float, float, float]] = {}
        for entity in doc.modelspace():
            points = _points(entity)
            if not points:
                continue
            xs = [round(p[0], self.precision) for p in points]
            ys = [round(p[1], self.precision) for p in points]
            key = (f"{entity.dxftype()}|{entity.dxf.layer}|"
                   + ";".join(f"{x},{y}" for x, y in zip(xs, ys, strict=False)))
            out[key] = (min(xs), min(ys), max(xs), max(ys))
        return out


def _points(entity) -> list[tuple[float, float]]:
    """Опорные точки примитива для построения подписи."""
    kind = entity.dxftype()
    try:
        if kind == "LINE":
            return [(entity.dxf.start.x, entity.dxf.start.y),
                    (entity.dxf.end.x, entity.dxf.end.y)]
        if kind in ("CIRCLE", "ARC"):
            return [(entity.dxf.center.x, entity.dxf.center.y)]
        if kind in ("TEXT", "MTEXT"):
            insert = entity.dxf.insert
            return [(insert.x, insert.y)]
        if kind in ("LWPOLYLINE", "POLYLINE"):
            return [(p[0], p[1]) for p in entity.get_points()]
    except AttributeError:
        return []
    return []
