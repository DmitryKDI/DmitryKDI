"""Подключение к базе данных и сессии.

В демонстрационном контуре — SQLite, в продуктиве — PostgreSQL по DATABASE_URL.
Схема и запросы одинаковы: используется ORM без конкатенации SQL.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models import Base

DEFAULT_SQLITE = "sqlite+aiosqlite:///data/nadzor.db"


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        Path("data").mkdir(exist_ok=True)
        return DEFAULT_SQLITE
    return url


_engine = None
_session_factory = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url(), echo=False, future=True)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False)
    return _session_factory


async def create_schema() -> None:
    """Создать схему. В продуктиве применяется миграциями Alembic."""
    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        yield session
