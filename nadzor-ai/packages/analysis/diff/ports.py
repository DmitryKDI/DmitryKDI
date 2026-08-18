"""Порт сравнения двух документов. Реализации: растровая и DXF."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

BBox = tuple[float, float, float, float]


@dataclass
class DiffRegion:
    """Изменённая область при сравнении двух листов."""
    page: int
    kind: str                       # added | removed | changed
    bbox_before: BBox | None
    bbox_after: BBox | None
    payload: str = ""
    weight: float = 0.0             # доля площади листа, для сортировки


@runtime_checkable
class DiffEngine(Protocol):
    """Контракт движка сравнения."""

    name: str
    supported_formats: frozenset[str]

    def compare(self, before: bytes, after: bytes) -> list[DiffRegion]: ...
