"""Матрица контроля — таблица Params в базе (ТЗ 8.1, модуль 8).

Исходная официальная матрица из 132 параметров лежит в
`data/parameter_catalog_v1_1.json` и при первом запуске переносится в таблицу
Params. Дальше администратор меняет пороги (min_value, max_value), нормативные
ссылки, шаблоны разбора и активность параметров через API — без
перекодирования системы. Каждая правка увеличивает номер ревизии матрицы, и
версия матрицы в протоколе показывает, по какой редакции шла проверка.
"""
from __future__ import annotations

import datetime as dt
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from .db import SessionLocal

PARAMETER_COUNT = 132
CATALOG_VERSION = "1.1"
CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "parameter_catalog_v1_1.json"

DATA_TYPES = ("number", "string", "boolean", "coordinate", "enum")
PRIORITIES = ("HIGH", "MEDIUM", "LOW")
# Поля Params, которые администратор может менять (ТЗ 7, модуль 8).
EDITABLE_FIELDS = ("trigger_logic", "review_priority", "sp_reference", "gost_reference",
                   "fz_reference", "other_normative", "data_type", "min_value", "max_value",
                   "regex_pattern", "is_active")


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("version") != CATALOG_VERSION:
        raise ValueError("Неизвестная версия официальной матрицы")
    parameters = payload["parameters"]
    expected = [f"M-{number:03d}" for number in range(1, PARAMETER_COUNT + 1)]
    if [item.get("code") for item in parameters] != expected:
        raise ValueError("Каталог должен содержать все коды официальной матрицы по порядку")
    fields = ("name", "description", "section", "unit", "source_pd", "source_rd", "source_id",
              "trigger")
    for item in parameters:
        if item.get("priority") not in {"HIGH", "MEDIUM"}:
            raise ValueError("Неизвестный приоритет параметра")
        if item.get("version") != CATALOG_VERSION:
            raise ValueError("Версия параметра не совпадает с версией каталога")
        if any(not isinstance(item.get(field), str) or not item[field] for field in fields):
            raise ValueError("Не заполнено обязательное поле параметра")
    return {item["code"]: item for item in parameters}


def _section_code(section: str) -> str:
    """«Раздел 1. ПЗ» → «ПЗ»: в таблице Params раздел — условное обозначение."""
    return section.split(".", 1)[-1].strip() if "." in section else section.strip()


def _data_type(unit: str) -> str:
    """Тип значения по единице измерения — начальное значение, его правит администратор."""
    if "Коорд" in unit:
        return "coordinate"
    if any(word in unit for word in ("Марка", "Класс", "Кат", "Степень", "Буква", "RAL",
                                     "Статус")):
        return "enum"
    if unit.strip() in {"", "—"}:
        return "string"
    return "number"


def _ensure_tables() -> None:
    """Таблицы матрицы — и при вызове проверки без запуска сервиса (скрипты, CLI)."""
    from . import models
    from .db import engine

    models.Base.metadata.create_all(bind=engine, tables=[
        models.Param.__table__, models.Settings.__table__])


def ensure_seeded() -> None:
    """Перенести официальную матрицу в таблицу Params, если таблица пуста."""
    from . import models

    _ensure_tables()
    with SessionLocal() as db:
        if db.query(models.Param).count():
            return
        now = dt.datetime.utcnow()
        for item in _catalog().values():
            db.add(models.Param(
                code=item["code"], section=_section_code(item["section"]),
                parameter_name=item["name"], description=item["description"],
                unit=item["unit"], source_pd=item["source_pd"], source_rd=item["source_rd"],
                source_id=item["source_id"], trigger_logic=item["trigger"],
                review_priority=item["priority"], data_type=_data_type(item["unit"]),
                is_active=True, created_at=now, updated_at=now,
            ))
        db.commit()


def param_dict(row) -> dict:
    """Параметр в том виде, в каком его читает сверка и интерфейс."""
    return {
        "id": row.id, "code": row.code, "section": row.section, "name": row.parameter_name,
        "parameter_name": row.parameter_name, "description": row.description,
        "unit": row.unit, "source_pd": row.source_pd, "source_rd": row.source_rd,
        "source_id": row.source_id, "trigger": row.trigger_logic,
        "trigger_logic": row.trigger_logic, "priority": row.review_priority,
        "review_priority": row.review_priority, "sp_reference": row.sp_reference,
        "gost_reference": row.gost_reference, "fz_reference": row.fz_reference,
        "other_normative": row.other_normative, "data_type": row.data_type,
        "min_value": row.min_value, "max_value": row.max_value,
        "regex_pattern": row.regex_pattern, "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        # Версия официального параметра; редакция матрицы целиком (с правками
        # администратора) — `current_matrix_version()`, она пишется в протокол.
        "version": CATALOG_VERSION,
    }


def list_parameters(*, include_inactive: bool = False) -> list[dict]:
    """Параметры матрицы по порядку; неактивные в проверку не идут."""
    from . import models

    ensure_seeded()
    with SessionLocal() as db:
        rows = db.query(models.Param).order_by(models.Param.id).all()
        return [param_dict(row) for row in rows if include_inactive or row.is_active]


def get_parameter(code: str) -> dict:
    """Возвращает параметр; неизвестный код не заменяется ближайшим или вымышленным."""
    for item in list_parameters(include_inactive=True):
        if item["code"] == code:
            return deepcopy(item)
    raise KeyError(code)


def current_matrix_version() -> str:
    """Версия матрицы: официальная версия и номер правки администратором."""
    from . import models

    _ensure_tables()
    with SessionLocal() as db:
        settings = db.query(models.Settings).first()
        revision = settings.matrix_revision if settings is not None else 0
    return CATALOG_VERSION if not revision else f"{CATALOG_VERSION}-r{revision}"


def bump_matrix_revision(db) -> None:
    """Правка матрицы администратором — новая ревизия (вызывается в той же транзакции)."""
    from . import models

    settings = db.query(models.Settings).first()
    if settings is not None:
        settings.matrix_revision = (settings.matrix_revision or 0) + 1
