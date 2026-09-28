"""Журнал аудита действий пользователей (ТЗ 7, модуль 9; 12, п.4).

Каждое действие, меняющее данные, и каждая попытка входа записываются с
временем, IP-адресом, типом действия, идентификатором объекта и
пользователем. Запись делается промежуточным слоем после ответа, поэтому ни
один обработчик не может её «забыть»: достаточно того, что запрос пришёл.

Журнал только дополняется: ни эндпоинта изменения, ни удаления записей нет.
"""
from __future__ import annotations

import datetime as dt

from fastapi import Request

from . import models
from .db import SessionLocal

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _client_ip(request: Request) -> str:
    # За обратным прокси интерфейса адрес клиента приходит заголовком.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def should_record(request: Request) -> bool:
    return request.method in _MUTATING


def record(request: Request, status_code: int) -> None:
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    params = request.scope.get("path_params") or {}
    object_id = next((str(params[key]) for key in
                      ("process_id", "run_id", "document_id", "user_id", "code", "item_id")
                      if key in params), "")
    principal = getattr(request.state, "principal", None)
    details = dict(getattr(request.state, "audit_details", {}) or {})
    db = SessionLocal()
    try:
        db.add(models.AuditLog(
            user_id=getattr(principal, "user_id", None),
            login=getattr(principal, "login", "") or details.pop("login", ""),
            action=f"{request.method} {path}", object_id=object_id, details=details,
            status_code=status_code, timestamp=dt.datetime.utcnow(),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", "")[:500],
        ))
        db.commit()
    finally:
        db.close()


def note(request: Request, **details) -> None:
    """Добавить подробности к записи аудита текущего запроса."""
    current = dict(getattr(request.state, "audit_details", {}) or {})
    current.update(details)
    request.state.audit_details = current
