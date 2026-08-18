"""Разграничение доступа. Принцип «запрещено по умолчанию».

Проверка выполняется на уровне доступа к данным: запрос без явной проверки
прав не должен возвращать строки. Ограничения интерфейса самостоятельной
защитой не считаются.
"""
from __future__ import annotations

from sqlalchemy import Select

from api.models import ConstructionObject
from integrations.identity.ports import Principal

# Действие → роли, которым оно разрешено.
PERMISSIONS: dict[str, set[str]] = {
    "objects:read": {"inspector", "head_of_dept", "expert", "analyst", "admin"},
    "objects:create": {"inspector", "head_of_dept", "admin"},
    "analysis:run": {"inspector", "head_of_dept", "admin"},
    "findings:read": {"inspector", "head_of_dept", "expert", "analyst", "admin"},
    "findings:assess": {"inspector", "head_of_dept"},
    "findings:reset": {"head_of_dept"},
    "reports:build": {"inspector", "head_of_dept"},
    "audit:read": {"auditor", "admin"},
    "analytics:read": {"analyst", "head_of_dept", "admin"},
    "admin:write": {"admin"},
    "integrations:read": {"admin", "head_of_dept", "analyst"},
}


class AccessDenied(Exception):
    """Отказ в доступе. Причина пользователю не раскрывается детально."""


def can(principal: Principal, action: str) -> bool:
    allowed = PERMISSIONS.get(action)
    return bool(allowed) and principal.has_role(*allowed)


def require(principal: Principal, action: str) -> None:
    if not can(principal, action):
        raise AccessDenied(f"Недостаточно прав для действия «{action}».")


def scope_objects(stmt: Select, principal: Principal) -> Select:
    """Ограничить выборку объектов правами сотрудника.

    Возвращает заведомо пустую выборку для ролей без доступа к объектам.
    """
    if principal.has_role("admin", "analyst", "expert"):
        return stmt
    if principal.has_role("head_of_dept"):
        return stmt.where(ConstructionObject.department == principal.department)
    if principal.has_role("inspector"):
        return stmt.where(ConstructionObject.assigned_to == principal.subject)
    return stmt.where(ConstructionObject.id == "__none__")


def visible_object_ids(objects: list[ConstructionObject], principal: Principal) -> set[str]:
    """Идентификаторы объектов, доступных сотруднику."""
    if principal.has_role("admin", "analyst", "expert"):
        return {o.id for o in objects}
    if principal.has_role("head_of_dept"):
        return {o.id for o in objects if o.department == principal.department}
    if principal.has_role("inspector"):
        return {o.id for o in objects if o.assigned_to == principal.subject}
    return set()
