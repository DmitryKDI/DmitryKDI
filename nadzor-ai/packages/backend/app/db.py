from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = os.environ.get("NADZOR_DB_PATH", str(Path(__file__).resolve().parents[1] / "nadzor.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def _schema_is_stale() -> bool:
    """Есть ли в файле базы таблица, где не хватает колонок нынешней модели.

    create_all() создаёт недостающие таблицы, но не меняет существующие: база
    от прошлой версии переживает запуск и падает на первом же запросе к новой
    колонке. Здесь это ловится заранее.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue
        actual = {c["name"] for c in inspector.get_columns(table.name)}
        if {c.name for c in table.columns} - actual:
            return True
    return False


def init_db() -> None:
    from . import models  # noqa: F401 — регистрирует модели в Base.metadata

    # База этого инструмента хранит только историю локальных прогонов и
    # пересоздаётся за секунды, поэтому пересборка схемы предпочтительнее
    # ручной миграции: альтернатива — заставлять пользователя удалять файл
    # руками после каждого обновления, о чём он узнаёт из ошибки в браузере.
    if _schema_is_stale():
        print("Схема базы устарела — пересоздаю. История прошлых прогонов будет очищена.")
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)


def get_session():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
