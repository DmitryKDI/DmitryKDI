"""Схема хранения — SQLite через SQLAlchemy ORM.

relationship() объявлен явно на каждой связи (а не только Column(ForeignKey))
— в этой же сессии проекта уже был такой баг,
где SQLAlchemy без relationship() не мог определить порядок вставки строк и
падал по внешнему ключу на реальном Postgres (SQLite это спускает, реальная
СУБД — нет). Здесь база тоже SQLite, но повторять ту же ошибку незачем.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
    # Отпечаток содержимого — ключ оригинала в отдельном хранилище
    # (`file_store`). Через него документ и файл связаны: два документа с
    # одинаковым содержимым ссылаются на один оригинал.
    digest: Mapped[str] = mapped_column(String, default="", index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    # Части тяжёлого тома: [{digest, first_page, pages}] в порядке листов.
    # Пусто — том целый. Нумерация листов наружу всегда исходная, части —
    # внутреннее устройство хранения (`document_split`).
    parts: Mapped[list] = mapped_column(JSON, default=list)
    # Исторические before/after не позволяют восстановить, где РД, а где ИД.
    # Поэтому стадию старым документам не приписываем без решения инспектора.
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_version: Mapped[int] = mapped_column(Integer, default=0)


class DocumentMetadataEvent(Base):
    __tablename__ = "document_metadata_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    document: Mapped[Document] = relationship()


class OfficialRun(Base):
    """Снимок входов и машинного ответа; решения эксперта хранятся отдельно."""
    __tablename__ = "official_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running")
    stage: Mapped[str] = mapped_column(String, default="Проверка редакций")
    completed: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    input_snapshot: Mapped[list] = mapped_column(JSON, default=list)
    include_medium: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Финализация протокола (ТЗ 9.3): после неё решения и дозагрузка закрыты.
    finalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finalized_by: Mapped[str] = mapped_column(String, default="")
    # Передача во внешнюю систему. В закрытом контуре внешнего адреса нет,
    # поэтому состояние честно «не отправлялось», а не «отправлено».
    sync_status: Mapped[str] = mapped_column(String, default="NOT_SENT")
    # Прежние версии протокола при дозагрузке: новая версия не затирает
    # предыдущую (ТЗ 9.2, инкрементальное обновление).
    protocol_history: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String, default="")


class ProtocolEvent(Base):
    """Журнал действий с протоколом: финализация, отмена, дозагрузка."""
    __tablename__ = "protocol_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    action: Mapped[str] = mapped_column(String)
    author: Mapped[str] = mapped_column(String, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class InspectorDecision(Base):
    __tablename__ = "inspector_decisions"
    __table_args__ = (UniqueConstraint("run_id", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    version: Mapped[int] = mapped_column(Integer)
    finding_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    author: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    # Кодированная причина решения (ТЗ 9.3): обязательна при отклонении.
    reason_code: Mapped[str] = mapped_column(String, default="")
    # Кто принял решение (ТЗ 9.3: решение содержит user_id, timestamp, комментарий).
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    run: Mapped[OfficialRun] = relationship()


class Settings(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Модель только локальная; строка сохраняется для совместимости схемы,
    # но модель выбирает окружение развёртывания (local_config).
    provider: Mapped[str] = mapped_column(String, default="local")
    base_url: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    # Сроки и лимиты хранения (Б.4/Б.5). Значения по умолчанию — бюджеты, а
    # не пороги истины: их меняет администратор под свой стенд, и от них не
    # зависит правильность разбора, только сколько места и времени он берёт.
    # Единица — килобайт, а не мегабайт: интерфейс показывает мегабайты,
    # но в килобайтах предел можно задать и для маленького стенда, и
    # проверить тестом, не собирая гигабайтный файл ради проверки лимита.
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    max_upload_kb: Mapped[int] = mapped_column(Integer, default=512 * 1024)
    max_pages: Mapped[int] = mapped_column(Integer, default=5000)
    part_kb: Mapped[int] = mapped_column(Integer, default=32 * 1024)


class User(Base):
    """Пользователь с логином и паролем (ТЗ 12, п.1–2)."""
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    """Сессия входа; хранится SHA-256 токена, а не сам токен."""
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    user: Mapped[User] = relationship()


class AuditLog(Base):
    """Журнал аудита действий пользователей (ТЗ 10, таблица Audit_Log; 12, п.4)."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    login: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String)
    object_id: Mapped[str] = mapped_column(String, default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow,
                                                   index=True)
    ip_address: Mapped[str] = mapped_column(String, default="")
    user_agent: Mapped[str] = mapped_column(String, default="")
