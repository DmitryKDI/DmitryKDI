"""Порт единой системы идентификации. Реализации: СУДИР и мок."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Внутренние роли системы. Роли, присланные внешней системой, отображаются на них.
ROLES = {
    "inspector": "Инспектор",
    "head_of_dept": "Начальник отдела",
    "expert": "Эксперт ЦЭИИС",
    "analyst": "Аналитик",
    "admin": "Администратор",
    "auditor": "Аудитор",
}


@dataclass
class Principal:
    """Сотрудник и его полномочия. Источник истины — внешняя система."""
    subject: str
    full_name: str
    department: str = ""
    position: str = ""
    roles: list[str] = field(default_factory=list)
    valid_until: str = ""
    source: str = "mock"

    def has_role(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


@dataclass
class IdentityToken:
    value: str
    expires_at: str = ""
    refresh: str = ""


@runtime_checkable
class IdentityProvider(Protocol):
    """Контракт провайдера идентификации."""

    name: str

    async def authorization_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(self, code: str, redirect_uri: str) -> IdentityToken: ...

    async def fetch_principal(self, token: IdentityToken) -> Principal: ...

    async def logout_url(self, token: IdentityToken) -> str: ...

    async def is_revoked(self, subject: str) -> bool: ...
