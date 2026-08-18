"""Зависимости маршрутов: сессия, текущий сотрудник, проверка прав."""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, status
from integrations.identity.ports import IdentityToken, Principal
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import get_session
from api.rbac import AccessDeniedError, require
from api.state import state


async def session_dep() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


async def current_principal(authorization: str = Header(default="")) -> Principal:
    """Текущий сотрудник по токену единого входа. Локальных паролей нет."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Требуется вход через единую систему идентификации.")
    try:
        return await state.identity.fetch_principal(IdentityToken(value=token))
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def permission(action: str):
    """Проверка права на действие до обращения к данным."""
    async def _check(principal: Principal = Depends(current_principal)) -> Principal:
        try:
            require(principal, action)
        except AccessDeniedError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return principal
    return _check
