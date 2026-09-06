"""Вход через единую систему идентификации. Локальных паролей нет."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from integrations.identity.ports import ROLES, IdentityToken, Principal
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit_store import record
from api.deps import current_principal, session_dep
from api.rbac import PERMISSIONS
from api.state import state

router = APIRouter(prefix="/api/auth", tags=["Идентификация"])


@router.get("/accounts", summary="Учётные записи для демонстрации")
async def accounts() -> dict:
    """Список учётных записей мока СУДИР: переключение роли в один клик."""
    if not hasattr(state.identity, "principals"):
        raise HTTPException(status_code=404,
                            detail="Список учётных записей доступен только в демо-режиме.")
    return {"provider": state.identity.name, "roles": ROLES,
            "accounts": [p.__dict__ for p in state.identity.principals()]}


@router.post("/login", summary="Вход")
async def login(payload: dict, session: AsyncSession = Depends(session_dep)) -> dict:
    """Обмен кода на токен. В продуктиве код приходит с редиректа СУДИР."""
    code = payload.get("code") or f"mock:{payload.get('subject', '')}"
    token = await state.identity.exchange_code(code, payload.get("redirect_uri", "/"))
    try:
        principal = await state.identity.fetch_principal(token)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if await state.identity.is_revoked(principal.subject):
        raise HTTPException(status_code=401, detail="Права учётной записи отозваны.")
    record(session, principal.subject, principal.roles[0] if principal.roles else "",
           "auth.login", "user", principal.subject, {"provider": state.identity.name})
    await session.commit()
    # Значение «Bearer» — название схемы передачи токена, а не секрет.
    return {"access_token": token.value, "token_type": "Bearer",  # nosec B105
            "principal": principal.__dict__, "permissions": _permissions(principal)}


@router.get("/me", summary="Текущий сотрудник")
async def me(principal: Principal = Depends(current_principal)) -> dict:
    return {"principal": principal.__dict__, "permissions": _permissions(principal),
            "roles_ru": {r: ROLES.get(r, r) for r in principal.roles}}


@router.post("/logout", summary="Единый выход")
async def logout(principal: Principal = Depends(current_principal),
                 session: AsyncSession = Depends(session_dep)) -> dict:
    url = await state.identity.logout_url(IdentityToken(value=""))
    record(session, principal.subject, principal.roles[0] if principal.roles else "",
           "auth.logout", "user", principal.subject)
    await session.commit()
    return {"logout_url": url}


def _permissions(principal: Principal) -> list[str]:
    return sorted(action for action, roles in PERMISSIONS.items()
                  if principal.has_role(*roles))
