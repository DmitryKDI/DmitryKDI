"""Порт ИАИС ОГД. Реализации: реальная система и мок.

Каждый ответ несёт поле source: интерфейс обязан показывать, подтверждены
сведения внешней системой или введены вручную.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ObjectCard:
    """Карточка объекта капитального строительства."""
    permit_number: str
    permit_date: str
    name: str
    address: str
    district: str = ""
    cadastral_number: str = ""
    gpzu: str = ""
    floors: int = 0
    area_m2: int = 0
    developer: str = ""
    technical_customer: str = ""
    contractor: str = ""
    designer: str = ""
    stage: str = ""
    planned_completion: str = ""
    expertise: dict = field(default_factory=dict)
    sro: dict = field(default_factory=dict)
    responsible: dict = field(default_factory=dict)
    source: str = "mock"


@dataclass
class InspectionRecord:
    date: str
    kind: str
    result: str
    prescription: str = ""
    deadline: str = ""
    status: str = ""


@runtime_checkable
class UrbanDataProvider(Protocol):
    """Контракт поставщика градостроительных данных."""

    name: str

    async def object_by_permit(self, permit_number: str) -> ObjectCard | None: ...

    async def permit_history(self, permit_number: str) -> list[dict]: ...

    async def expertise_conclusions(self, permit_number: str) -> list[dict]: ...

    async def participants(self, permit_number: str) -> dict: ...

    async def inspection_history(self, permit_number: str) -> list[InspectionRecord]: ...
