from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from sqlalchemy import JSON, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = os.environ.get("NADZOR_DB_PATH", str(Path(__file__).resolve().parents[1] / "nadzor.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# Настройки — не история прогонов, а конфигурация машины: провайдер, адрес,
# модель и ключ, введённый человеком руками. Пересборка схемы имеет право
# очистить прогоны, но не имеет права молча стереть ключ (Г.113).
_PRESERVED = ("settings",)


def _missing_columns() -> dict[str, list]:
    """Колонки нынешней модели, которых нет в файле базы, по таблицам.

    create_all() создаёт недостающие таблицы, но не меняет существующие: база
    от прошлой версии переживает запуск и падает на первом же запросе к новой
    колонке. Здесь это ловится заранее.
    """
    from . import models  # noqa: F401 — регистрирует модели в Base.metadata

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing: dict[str, list] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        actual = {c["name"] for c in inspector.get_columns(table.name)}
        gap = [c for c in table.columns if c.name not in actual]
        if gap:
            missing[table.name] = gap
    return missing


def _schema_is_stale() -> bool:
    return bool(_missing_columns())


_UNRESOLVED = object()


def _default_value(column):
    """Значение новой колонки для уже существующих строк.

    SQLite не даёт добавить NOT NULL-колонку без значения по умолчанию, а
    значения по умолчанию в моделях описаны на стороне Python — и часть из
    них вычисляемые (`list`, `utcnow`). Вычисляемые вызываются здесь же:
    иначе добавление любого поля со списком или временем отправляло бы базу
    на полную пересборку, то есть стоило бы пользователю всех его данных.
    """
    default = column.default
    if default is None:
        return None if column.nullable else _UNRESOLVED
    if getattr(default, "is_scalar", False):
        return default.arg
    if getattr(default, "is_callable", False):
        try:
            return default.arg(None)
        except Exception:  # noqa: BLE001 — значение зависит от строки, взять нечего
            return _UNRESOLVED
    return _UNRESOLVED


def _sql_literal(value, column) -> str | None:
    """Значение как литерал SQLite, или None, если записать его нельзя."""
    if value is _UNRESOLVED:
        return None
    if value is None:
        return "NULL"
    if isinstance(column.type, JSON):
        return _quote(json.dumps(value, ensure_ascii=False))
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return _quote(value.isoformat(sep=" "))
    return None


def _quote(text_value: str) -> str:
    return "'" + text_value.replace("'", "''") + "'"


def _default_literal(column) -> str | None:
    return _sql_literal(_default_value(column), column)


def _add_column_sql(table_name: str, column) -> str | None:
    """ALTER TABLE для одной колонки, или None, если добавить её нельзя."""
    literal = _default_literal(column)
    if literal is None:
        return None
    try:
        column_type = column.type.compile(engine.dialect)
    except Exception:  # noqa: BLE001 — экзотический тип: дописать не выйдет
        return None
    null = "" if column.nullable else " NOT NULL"
    return (f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" '
            f"{column_type}{null} DEFAULT {literal}")


def _extend_schema() -> list[str]:
    """Дописать недостающие колонки. Возвращает то, что дописать не удалось."""
    unresolved: list[str] = []
    for table_name, columns in _missing_columns().items():
        for column in columns:
            statement = _add_column_sql(table_name, column)
            if statement is None:
                unresolved.append(f"{table_name}.{column.name}")
                continue
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(statement)
            except Exception as exc:  # noqa: BLE001 — причина неважна, важен факт
                print(f"колонка {table_name}.{column.name} не добавлена: {exc}")
                unresolved.append(f"{table_name}.{column.name}")
    return unresolved


def _snapshot_preserved() -> dict[str, list[dict]]:
    """Содержимое таблиц-настроек перед пересборкой схемы."""
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    saved: dict[str, list[dict]] = {}
    for name in _PRESERVED:
        if name not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(name)}
        model = {c.name for c in Base.metadata.tables[name].columns}
        keep = sorted(columns & model)
        if not keep:
            continue
        quoted = ", ".join(f'"{c}"' for c in keep)
        with engine.begin() as conn:
            rows = conn.execute(text(f'SELECT {quoted} FROM "{name}"')).mappings().all()
        saved[name] = [dict(row) for row in rows]
    return saved


def _restore_preserved(saved: dict[str, list[dict]]) -> None:
    """Вернуть настройки в пересозданную таблицу.

    Вставка идёт через модель, а не голым SQL: колонки, появившиеся в новой
    версии, получают значения по умолчанию из модели, иначе перенос падал бы
    на первом же NOT NULL-поле — и «сохранение настроек» на деле означало бы
    их потерю.
    """
    for name, rows in saved.items():
        if not rows:
            continue
        table = Base.metadata.tables[name]
        with engine.begin() as conn:
            conn.execute(table.insert(), rows)


def init_db() -> None:
    from . import models  # noqa: F401 — регистрирует модели в Base.metadata

    # Порядок важен. Сначала пытаемся дописать недостающие колонки: обновление
    # инструмента почти всегда только добавляет поля, и терять из-за этого
    # разобранные тома и введённый ключ незачем. Пересборка схемы — запасной
    # путь на случай, который дописыванием не решается; тогда настройки
    # переносятся отдельно, потому что ключ вводится руками и восстановить
    # его из кода нельзя (Г.113).
    unresolved = _extend_schema()
    if unresolved:
        print("Схема базы устарела — пересоздаю. История прошлых прогонов будет очищена: "
              + ", ".join(unresolved))
        saved = _snapshot_preserved()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        _restore_preserved(saved)
        return

    Base.metadata.create_all(bind=engine)


def get_session():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
