"""Управление матрицей и нормативной базой (ТЗ 7, модуль 8).

Администратор добавляет, редактирует и деактивирует нормативные ссылки и
логические правила, обновляет пороги параметров (min_value, max_value) без
перекодирования системы. Удаления нет: запись деактивируется, чтобы прежние
протоколы оставались объяснимыми. Каждая правка матрицы — новая ревизия,
которая попадает в версию матрицы следующего протокола.
"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import auth, models, parameter_catalog
from .db import get_session

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
ADMIN = Depends(auth.require("admin"))
READ = Depends(auth.require(*auth.READERS))


class ParamUpdate(BaseModel):
    trigger_logic: str | None = None
    review_priority: str | None = None
    sp_reference: str | None = None
    gost_reference: str | None = None
    fz_reference: str | None = None
    other_normative: str | None = None
    data_type: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    regex_pattern: str | None = None
    is_active: bool | None = None
    # Явный сброс порога: null в min_value/max_value неотличим от «не менять».
    clear_min_value: bool = False
    clear_max_value: bool = False


class NormInput(BaseModel):
    document_name: str
    document_number: str
    section: str = ""
    parameter_name: str = ""
    min_value: float | None = None
    max_value: float | None = None
    effective_from: dt.date | None = None
    effective_to: dt.date | None = None
    is_active: bool = True


class RuleInput(BaseModel):
    rule_name: str
    condition: str
    expected: str
    normative_base: str = ""
    review_priority: str = "MEDIUM"
    is_active: bool = True


def _check_range(low: float | None, high: float | None) -> None:
    if low is not None and high is not None and low > high:
        raise HTTPException(422, "min_value больше max_value")


@router.get("/params", summary="Параметры матрицы (включая неактивные)", dependencies=[READ])
def params():
    return {"matrix_version": parameter_catalog.current_matrix_version(),
            "parameters": parameter_catalog.list_parameters(include_inactive=True)}


@router.patch("/params/{code}", summary="Изменить параметр матрицы", dependencies=[ADMIN])
def update_param(code: str, body: ParamUpdate, db: Session = Depends(get_session)):
    row = db.query(models.Param).filter_by(code=code).first()
    if row is None:
        raise HTTPException(404, "параметр не найден")
    changes = body.model_dump(exclude_unset=True)
    if "data_type" in changes and changes["data_type"] not in parameter_catalog.DATA_TYPES:
        raise HTTPException(422, "тип: " + ", ".join(parameter_catalog.DATA_TYPES))
    if "review_priority" in changes and changes["review_priority"] not in \
            parameter_catalog.PRIORITIES:
        raise HTTPException(422, "приоритет: " + ", ".join(parameter_catalog.PRIORITIES))
    if changes.get("regex_pattern"):
        try:
            re.compile(changes["regex_pattern"])
        except re.error as exc:
            raise HTTPException(422, f"регулярное выражение с ошибкой: {exc}") from exc
    for field in parameter_catalog.EDITABLE_FIELDS:
        if field in changes and changes[field] is not None:
            setattr(row, field, changes[field])
    if body.clear_min_value:
        row.min_value = None
    if body.clear_max_value:
        row.max_value = None
    _check_range(row.min_value, row.max_value)
    row.updated_at = dt.datetime.utcnow()
    parameter_catalog.bump_matrix_revision(db)
    db.commit()
    db.refresh(row)
    return parameter_catalog.param_dict(row)


def _norm_dict(row: models.NormativeBase) -> dict:
    return {"id": row.id, "document_name": row.document_name,
            "document_number": row.document_number, "section": row.section,
            "parameter_name": row.parameter_name, "min_value": row.min_value,
            "max_value": row.max_value,
            "effective_from": row.effective_from.isoformat() if row.effective_from else None,
            "effective_to": row.effective_to.isoformat() if row.effective_to else None,
            "is_active": row.is_active}


@router.get("/normative", summary="Нормативная база", dependencies=[READ])
def normative(db: Session = Depends(get_session)):
    rows = db.query(models.NormativeBase).order_by(models.NormativeBase.id).all()
    return [_norm_dict(row) for row in rows]


def _apply_norm(row: models.NormativeBase, body: NormInput, db: Session) -> None:
    if not body.document_name.strip() or not body.document_number.strip():
        raise HTTPException(422, "укажите наименование и номер документа")
    _check_range(body.min_value, body.max_value)
    if body.effective_from and body.effective_to and body.effective_from > body.effective_to:
        raise HTTPException(422, "дата окончания действия раньше даты начала")
    if body.parameter_name and not db.query(models.Param).filter_by(
            code=body.parameter_name).first():
        raise HTTPException(422, "параметр матрицы с таким кодом не найден")
    for field, value in body.model_dump().items():
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    row.updated_at = dt.datetime.utcnow()


@router.post("/normative", summary="Добавить нормативную ссылку", dependencies=[ADMIN])
def add_norm(body: NormInput, db: Session = Depends(get_session)):
    row = models.NormativeBase()
    _apply_norm(row, body, db)
    db.add(row)
    parameter_catalog.bump_matrix_revision(db)
    db.commit()
    db.refresh(row)
    return _norm_dict(row)


@router.put("/normative/{item_id}", summary="Изменить или деактивировать нормативную ссылку",
            dependencies=[ADMIN])
def edit_norm(item_id: int, body: NormInput, db: Session = Depends(get_session)):
    row = db.get(models.NormativeBase, item_id)
    if row is None:
        raise HTTPException(404, "нормативная ссылка не найдена")
    _apply_norm(row, body, db)
    parameter_catalog.bump_matrix_revision(db)
    db.commit()
    db.refresh(row)
    return _norm_dict(row)


def _rule_dict(row: models.LogicalRule) -> dict:
    return {"id": row.id, "rule_name": row.rule_name, "condition": row.condition,
            "expected": row.expected, "normative_base": row.normative_base,
            "review_priority": row.review_priority, "is_active": row.is_active}


@router.get("/rules", summary="Логические правила свободного поиска", dependencies=[READ])
def rules(db: Session = Depends(get_session)):
    return [_rule_dict(row) for row in
            db.query(models.LogicalRule).order_by(models.LogicalRule.id).all()]


def _apply_rule(row: models.LogicalRule, body: RuleInput) -> None:
    from .suspicions import parse_expression

    if not body.rule_name.strip():
        raise HTTPException(422, "укажите название правила")
    if body.review_priority not in parameter_catalog.PRIORITIES:
        raise HTTPException(422, "приоритет: " + ", ".join(parameter_catalog.PRIORITIES))
    for label, text in (("условие", body.condition), ("ожидание", body.expected)):
        try:
            parse_expression(text)
        except ValueError as exc:
            raise HTTPException(422, f"{label}: {exc}") from exc
    for field, value in body.model_dump().items():
        setattr(row, field, value.strip() if isinstance(value, str) else value)
    row.updated_at = dt.datetime.utcnow()


@router.post("/rules", summary="Добавить логическое правило", dependencies=[ADMIN])
def add_rule(body: RuleInput, db: Session = Depends(get_session)):
    row = models.LogicalRule()
    _apply_rule(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _rule_dict(row)


@router.put("/rules/{item_id}", summary="Изменить или деактивировать правило",
            dependencies=[ADMIN])
def edit_rule(item_id: int, body: RuleInput, db: Session = Depends(get_session)):
    row = db.get(models.LogicalRule, item_id)
    if row is None:
        raise HTTPException(404, "правило не найдено")
    _apply_rule(row, body)
    db.commit()
    db.refresh(row)
    return _rule_dict(row)
