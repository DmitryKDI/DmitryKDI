"""Адаптер СУДИР по протоколу OpenID Connect.

Точные адреса конечных точек и состав атрибутов уточняются у владельца
системы: перечень вопросов — в docs/integration-questions.md. Отображение
внешних ролей на внутренние задаётся конфигурацией, а не кодом.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx

from integrations.identity.ports import IdentityToken, Principal

DEFAULT_ROLE_MAP = {
    "GSN_INSPECTOR": "inspector",
    "GSN_HEAD": "head_of_dept",
    "CEIIS_EXPERT": "expert",
    "GSN_ANALYST": "analyst",
    "GSN_ADMIN": "admin",
    "GSN_AUDITOR": "auditor",
}


class SudirIdentityProvider:
    """Единый вход города по OIDC."""

    name = "sudir"

    def __init__(self, config: dict | None = None) -> None:
        config = config or {}
        self.issuer = config.get("issuer") or os.environ.get("SUDIR_ISSUER", "")
        self.client_id = config.get("client_id") or os.environ.get("SUDIR_CLIENT_ID", "")
        self.client_secret = os.environ.get("SUDIR_CLIENT_SECRET", "")
        self.scope = config.get("scope", "openid profile roles")
        self.role_map = config.get("role_map", DEFAULT_ROLE_MAP)
        self.timeout = config.get("timeout_seconds", 15)

    async def authorization_url(self, state: str, redirect_uri: str) -> str:
        params = {"response_type": "code", "client_id": self.client_id,
                  "redirect_uri": redirect_uri, "scope": self.scope, "state": state}
        return f"{self.issuer}/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> IdentityToken:
        data = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": redirect_uri, "client_id": self.client_id,
                "client_secret": self.client_secret}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.issuer}/token", data=data)
            r.raise_for_status()
            payload = r.json()
        return IdentityToken(value=payload["access_token"],
                             expires_at=str(payload.get("expires_in", "")),
                             refresh=payload.get("refresh_token", ""))

    async def fetch_principal(self, token: IdentityToken) -> Principal:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.issuer}/userinfo",
                                 headers={"Authorization": f"Bearer {token.value}"})
            r.raise_for_status()
            claims = r.json()
        roles = [self.role_map[r] for r in claims.get("roles", []) if r in self.role_map]
        return Principal(
            subject=claims.get("sub", ""), full_name=claims.get("name", ""),
            department=claims.get("department", ""), position=claims.get("title", ""),
            roles=roles, valid_until=claims.get("account_valid_until", ""), source="sudir")

    async def logout_url(self, token: IdentityToken) -> str:
        return f"{self.issuer}/logout"

    async def is_revoked(self, subject: str) -> bool:
        """Проверка отзыва прав. Конечная точка уточняется у владельца системы."""
        if not self.issuer:
            return False
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.issuer}/account-status", params={"sub": subject})
            if r.status_code != 200:
                return False
            return bool(r.json().get("revoked", False))
