from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DB_PATH = os.environ.get("NADZOR_DB_PATH", str(Path(__file__).resolve().parents[1] / "nadzor.db"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401 — регистрирует модели в Base.metadata

    Base.metadata.create_all(bind=engine)


def get_session():
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
