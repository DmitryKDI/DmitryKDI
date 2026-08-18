"""Мок ИАИС ОГД на данных демонстрационного комплекта."""
from __future__ import annotations

import json
from pathlib import Path

from integrations.urban_data.ports import InspectionRecord, ObjectCard

# История проверок за 8 месяцев: используется на карточке объекта и в аналитике.
INSPECTIONS = {
    "77-123456-012345-2024": [
        {"date": "2024-03-14", "kind": "Программная проверка", "result": "нарушений не выявлено"},
        {"date": "2024-05-23", "kind": "Программная проверка",
         "result": "выявлены нарушения", "prescription": "№ 1147/24 от 27.05.2024",
         "deadline": "2024-06-28", "status": "устранено"},
        {"date": "2024-07-11", "kind": "Внеплановая проверка",
         "result": "выявлены нарушения", "prescription": "№ 1583/24 от 15.07.2024",
         "deadline": "2024-08-30", "status": "устранено с нарушением срока"},
        {"date": "2024-09-26", "kind": "Программная проверка",
         "result": "выявлены нарушения", "prescription": "№ 2094/24 от 30.09.2024",
         "deadline": "2024-11-15", "status": "на контроле"},
    ],
    "77-234567-023456-2024": [
        {"date": "2024-06-05", "kind": "Программная проверка", "result": "нарушений не выявлено"},
        {"date": "2024-08-21", "kind": "Программная проверка",
         "result": "выявлены нарушения", "prescription": "№ 1802/24 от 26.08.2024",
         "deadline": "2024-09-30", "status": "устранено"},
    ],
    "77-345678-034567-2023": [
        {"date": "2025-02-12", "kind": "Программная проверка", "result": "нарушений не выявлено"},
        {"date": "2025-04-29", "kind": "Программная проверка",
         "result": "выявлены нарушения", "prescription": "№ 0712/25 от 06.05.2025",
         "deadline": "2025-06-16", "status": "на контроле"},
    ],
}


class MockUrbanDataProvider:
    """Провайдер градостроительных данных без внешней сети."""

    name = "mock"

    def __init__(self, objects: list[dict] | None = None) -> None:
        self._objects = {o["permit_number"]: o for o in (objects or [])}

    @classmethod
    def from_manifest(cls, path: str | Path) -> MockUrbanDataProvider:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("objects", []))

    def all_cards(self) -> list[ObjectCard]:
        return [self._card(o) for o in self._objects.values()]

    async def object_by_permit(self, permit_number: str) -> ObjectCard | None:
        record = self._objects.get(permit_number.strip())
        return self._card(record) if record else None

    async def permit_history(self, permit_number: str) -> list[dict]:
        record = self._objects.get(permit_number)
        if not record:
            return []
        return [{"number": record["permit_number"], "date": record["permit_date"],
                 "event": "выдача разрешения", "source": "mock"}]

    async def expertise_conclusions(self, permit_number: str) -> list[dict]:
        record = self._objects.get(permit_number)
        return [{**record["expertise"], "source": "mock"}] if record else []

    async def participants(self, permit_number: str) -> dict:
        record = self._objects.get(permit_number)
        if not record:
            return {}
        return {"developer": record["developer"],
                "technical_customer": record["technical_customer"],
                "contractor": record["contractor"], "designer": record["designer"],
                "sro": record["sro"], "responsible": record["responsible"], "source": "mock"}

    async def inspection_history(self, permit_number: str) -> list[InspectionRecord]:
        return [InspectionRecord(**item) for item in INSPECTIONS.get(permit_number, [])]

    @staticmethod
    def _card(record: dict) -> ObjectCard:
        fields = {k: v for k, v in record.items() if k in ObjectCard.__dataclass_fields__}
        return ObjectCard(**fields, source="mock")
