"""Мок СУДИР для разработки и демонстрации.

Позволяет переключить роль в один клик: один и тот же объект показывается
так, как его видит инспектор, начальник отдела или аудитор.
"""
from __future__ import annotations

import json
from pathlib import Path

from integrations.identity.ports import IdentityToken, Principal


class MockIdentityProvider:
    """Провайдер идентификации без внешней сети."""

    name = "mock"

    def __init__(self, staff: list[dict] | None = None) -> None:
        self._staff = {s["subject"]: s for s in (staff or [])}
        self._revoked: set[str] = set()

    @classmethod
    def from_manifest(cls, path: str | Path) -> MockIdentityProvider:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data.get("staff", []))

    def principals(self) -> list[Principal]:
        """Список учётных записей для экрана выбора роли в демо."""
        return [self._to_principal(s) for s in self._staff.values()]

    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        return f"{redirect_uri}?code=mock-code&state={state}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IdentityToken:
        subject = code.replace("mock:", "") if code.startswith("mock:") else "sudir:77001"
        return IdentityToken(value=f"mock-token:{subject}", expires_at="2027-12-31T23:59:59Z")

    async def fetch_principal(self, token: IdentityToken) -> Principal:
        # Отзыв прав проверяется вызывающей стороной через is_revoked() —
        # единая точка для всех реализаций IdentityProvider, а не только мока.
        subject = token.value.split(":", 1)[1] if ":" in token.value else ""
        subject = subject.replace("mock-token:", "")
        record = self._staff.get(subject)
        if record is None:
            raise PermissionError("Учётная запись не найдена в системе идентификации.")
        return self._to_principal(record)

    async def logout_url(self, token: IdentityToken) -> str:
        return "/login"

    async def is_revoked(self, subject: str) -> bool:
        return subject in self._revoked

    def revoke(self, subject: str) -> None:
        """Отзыв прав — для проверки корректной обработки в тестах."""
        self._revoked.add(subject)

    @staticmethod
    def _to_principal(record: dict) -> Principal:
        return Principal(subject=record["subject"], full_name=record["full_name"],
                         department=record.get("department", ""),
                         position=record.get("position", ""),
                         roles=list(record.get("roles", [])),
                         valid_until=record.get("valid_until", ""), source="mock")
