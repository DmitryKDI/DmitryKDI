"""Адаптер ИАИС ОГД.

Состав полей и адреса методов уточняются у владельца системы: перечень
вопросов — в docs/integration-questions.md. До подключения работает мок.
"""
from __future__ import annotations

import os

import httpx

from integrations.urban_data.ports import InspectionRecord, ObjectCard

FIELD_MAP = {
    "objectName": "name", "objectAddress": "address", "district": "district",
    "cadastralNumber": "cadastral_number", "gpzuNumber": "gpzu",
    "floorsCount": "floors", "totalArea": "area_m2", "developerName": "developer",
    "technicalCustomerName": "technical_customer", "builderName": "contractor",
    "designerName": "designer", "constructionStage": "stage",
    "plannedCompletionDate": "planned_completion",
}


class IaisOgdProvider:
    """Поставщик сведений об объектах капитального строительства."""

    name = "iais_ogd"

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.base_url = config.get("base_url") or os.environ.get("IAIS_OGD_BASE_URL", "")
        self.token = os.environ.get("IAIS_OGD_TOKEN", "")
        self.timeout = config.get("timeout_seconds", 20)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def _get(self, path: str, params: dict) -> dict | list | None:
        if not self.base_url:
            return None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}{path}", params=params, headers=self._headers())
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()

    async def object_by_permit(self, permit_number: str) -> ObjectCard | None:
        data = await self._get("/objects", {"permitNumber": permit_number})
        if not data:
            return None
        payload = data[0] if isinstance(data, list) else data
        mapped = {FIELD_MAP[k]: v for k, v in payload.items() if k in FIELD_MAP}
        return ObjectCard(permit_number=permit_number,
                          permit_date=payload.get("permitDate", ""), source="iais_ogd", **mapped)

    async def permit_history(self, permit_number: str) -> list[dict]:
        return await self._get("/permits/history", {"permitNumber": permit_number}) or []

    async def expertise_conclusions(self, permit_number: str) -> list[dict]:
        return await self._get("/expertise", {"permitNumber": permit_number}) or []

    async def participants(self, permit_number: str) -> dict:
        return await self._get("/participants", {"permitNumber": permit_number}) or {}

    async def inspection_history(self, permit_number: str) -> list[InspectionRecord]:
        items = await self._get("/inspections", {"permitNumber": permit_number}) or []
        return [InspectionRecord(date=i.get("date", ""), kind=i.get("kind", ""),
                                 result=i.get("result", ""),
                                 prescription=i.get("prescriptionNumber", ""),
                                 deadline=i.get("deadline", ""), status=i.get("status", ""))
                for i in items]
