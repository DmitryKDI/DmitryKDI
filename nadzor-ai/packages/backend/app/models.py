"""Схема хранения — SQLite через SQLAlchemy ORM.

relationship() объявлен явно на каждой связи (а не только Column(ForeignKey))
— в этой же сессии проекта уже был баг в соседнем пакете (packages/api),
где SQLAlchemy без relationship() не мог определить порядок вставки строк и
падал по внешнему ключу на реальном Postgres (SQLite это спускает, реальная
СУБД — нет). Здесь база тоже SQLite, но повторять ту же ошибку незачем.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    side: Mapped[str] = mapped_column(String)  # 'before' | 'after'
    file_path: Mapped[str] = mapped_column(String)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    discipline_code: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_source: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="parsing")  # parsing|ok|error
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    status: Mapped[str] = mapped_column(String, default="running")  # running|done|error
    before_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    after_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    pairs_total: Mapped[int] = mapped_column(Integer, default=0)
    pairs_done: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    pairs: Mapped[list["PagePair"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    findings: Mapped[list["Finding"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class PagePair(Base):
    __tablename__ = "page_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"))
    before_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    before_page: Mapped[int] = mapped_column(Integer)
    after_document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    after_page: Mapped[int] = mapped_column(Integer)
    matched_by: Mapped[str] = mapped_column(String)  # 'text' | 'position'
    page_kind: Mapped[str] = mapped_column(String, default="drawing")  # 'drawing' | 'text'
    score: Mapped[float] = mapped_column(Float, default=0.0)
    discipline_mismatch: Mapped[bool] = mapped_column(default=False)

    run: Mapped[AnalysisRun] = relationship(back_populates="pairs")
    before_document: Mapped[Document] = relationship(foreign_keys=[before_document_id])
    after_document: Mapped[Document] = relationship(foreign_keys=[after_document_id])
    findings: Mapped[list["Finding"]] = relationship(back_populates="pair")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id"))
    pair_id: Mapped[int | None] = mapped_column(ForeignKey("page_pairs.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String)  # 'text' | 'vision'
    label: Mapped[str] = mapped_column(String)
    change_text: Mapped[str] = mapped_column(String)
    # Порядок обхода объекта и действие на месте. Пустая строка, а не NULL:
    # модель может не вернуть поле, и на экране это должно читаться как
    # «не указано», без ветвления на None в каждом месте.
    severity: Mapped[str] = mapped_column(String, default="")
    field_check: Mapped[str] = mapped_column(String, default="")
    raw_llm_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reviewed_status: Mapped[str] = mapped_column(String, default="new")  # new|confirmed|rejected
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    run: Mapped[AnalysisRun] = relationship(back_populates="findings")
    pair: Mapped[PagePair | None] = relationship(back_populates="findings")

    # Денормализованный доступ к листу-источнику для фронтенда (картинка
    # листа к находке) — без пары (текстовая находка старого вида, больше не
    # создаётся, но старые записи в БД могут остаться) просто None.
    @property
    def after_document_id(self) -> int | None:
        return self.pair.after_document_id if self.pair else None

    @property
    def after_page(self) -> int | None:
        return self.pair.after_page if self.pair else None

    @property
    def before_document_id(self) -> int | None:
        return self.pair.before_document_id if self.pair else None

    @property
    def before_page(self) -> int | None:
        return self.pair.before_page if self.pair else None


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String, default="local")
    base_url: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    api_key: Mapped[str] = mapped_column(String, default="")
