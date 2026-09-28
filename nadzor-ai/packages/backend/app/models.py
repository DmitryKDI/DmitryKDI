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
    Date,
    DateTime,
    Float,
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
    # Повторы передачи (ТЗ 9.6): пакет, ключ идемпотентности, число попыток и
    # срок следующей — в самом процессе, чтобы пережить перезапуск сервиса.
    sync_package: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sync_key: Mapped[str] = mapped_column(String, default="")
    sync_attempts: Mapped[int] = mapped_column(Integer, default=0)
    sync_next_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # Документы, пришедшие после финализации (ТЗ 9.6): проверку не запускают,
    # инспектор получает уведомление и сам решает, создавать ли новую.
    pending_documents: Mapped[list] = mapped_column(JSON, default=list)
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
    # Номер правки матрицы администратором: версия матрицы в протоколе
    # меняется вместе с порогами и ссылками (ТЗ 9.2, п.1).
    matrix_revision: Mapped[int] = mapped_column(Integer, default=0)
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


class Param(Base):
    """Параметр матрицы контроля (ТЗ 8.1, таблица Params)."""
    __tablename__ = "params"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    section: Mapped[str] = mapped_column(String(50))
    parameter_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(20), default="")
    source_pd: Mapped[str] = mapped_column(Text, default="")
    source_rd: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(Text, default="")
    trigger_logic: Mapped[str] = mapped_column(Text, default="")
    review_priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    sp_reference: Mapped[str] = mapped_column(Text, default="")
    gost_reference: Mapped[str] = mapped_column(Text, default="")
    fz_reference: Mapped[str] = mapped_column(Text, default="")
    other_normative: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(20), default="string")
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    regex_pattern: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class NormativeBase(Base):
    """База нормативных документов (ТЗ 10, таблица Normative_Base)."""
    __tablename__ = "normative_base"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_name: Mapped[str] = mapped_column(Text)
    document_number: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String, default="")
    # Код параметра матрицы, к которому относится норма (пусто — общая норма).
    parameter_name: Mapped[str] = mapped_column(String, default="")
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class LogicalRule(Base):
    """База логических правил для свободного поиска (ТЗ 10, таблица Logical_Rules)."""
    __tablename__ = "logical_rules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_name: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    normative_base: Mapped[str] = mapped_column(Text, default="")
    review_priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class Suspicion(Base):
    """Подозрения свободного поиска (ТЗ 10, таблица Suspicions; 9.5).

    Не нарушение: не входит в число нарушений и не идёт в обучающую выборку.
    В CANDIDATE переводится только с источниками и координатами на листах
    обеих стадий; нарушением её делает только решение инспектора.
    """
    __tablename__ = "suspicions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    object_id: Mapped[str] = mapped_column(String)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    discovery_method: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str] = mapped_column(Text)
    pd_reference: Mapped[str] = mapped_column(String, default="")
    rd_reference: Mapped[str] = mapped_column(String, default="")
    review_priority: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    normative_base: Mapped[str] = mapped_column(Text, default="")
    parameter_code: Mapped[str] = mapped_column(String, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    finding_status: Mapped[str] = mapped_column(String(30), default="SUSPICION")
    inspector_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    inspector_comment: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class DatasetItem(Base):
    """Запись GOLD-набора (ТЗ 9.4, 14.1): одна evidence_group с решением эксперта.

    Положительная метка — только CONFIRMED_VIOLATION, отрицательная — только
    NEGATIVE_VERIFIED, обе с полным комплектом доказательств. Запись попадает
    в черновик сразу после решения, в выпуск набора — только после проверки
    куратором и финализации протокола.
    """
    __tablename__ = "dataset_items"
    __table_args__ = (UniqueConstraint("run_id", "finding_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    object_id: Mapped[str] = mapped_column(String)
    finding_id: Mapped[str] = mapped_column(String)
    parameter_code: Mapped[str] = mapped_column(String, default="")
    label: Mapped[str] = mapped_column(String(20))  # POSITIVE | NEGATIVE
    decision_version: Mapped[int] = mapped_column(Integer)
    reason_code: Mapped[str] = mapped_column(String, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    machine_status: Mapped[str] = mapped_column(String, default="")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    source_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    # DRAFT → APPROVED | EXCLUDED (решение куратора); SUPERSEDED — решение изменено.
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    curated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    curated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class ObjectSplit(Base):
    """Разбиение по объектам (ТЗ 14.2): объект навсегда в одном наборе."""
    __tablename__ = "object_splits"
    object_id: Mapped[str] = mapped_column(String, primary_key=True)
    split: Mapped[str] = mapped_column(String(10))  # train | validation | test
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class DatasetVersion(Base):
    """Выпуск набора данных: состав и хеши наборов фиксируются при выпуске."""
    __tablename__ = "dataset_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String, unique=True)
    matrix_version: Mapped[str] = mapped_column(String, default="")
    item_ids: Mapped[list] = mapped_column(JSON, default=list)
    split_hashes: Mapped[dict] = mapped_column(JSON, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class RejectionLog(Base):
    """Лог отклонений для дообучения (ТЗ 10, таблица Rejection_Log)."""
    __tablename__ = "rejection_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    violation_id: Mapped[str] = mapped_column(String)
    parameter_code: Mapped[str] = mapped_column(String, default="")
    rejection_reason: Mapped[str] = mapped_column(String, default="")
    inspector_comment: Mapped[str] = mapped_column(Text, default="")
    ai_verdict: Mapped[str] = mapped_column(Text, default="")
    suggested_fix: Mapped[str] = mapped_column(Text, default="")
    retraining_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class DisputeLog(Base):
    """Спорные случаи (ТЗ 10, таблица Dispute_Log): запрос уточнения."""
    __tablename__ = "dispute_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("official_runs.id"))
    violation_id: Mapped[str] = mapped_column(String)
    inspector_comment: Mapped[str] = mapped_column(Text, default="")
    ai_comment: Mapped[str] = mapped_column(Text, default="")
    resolution_status: Mapped[str] = mapped_column(String(20), default="OPEN")
    resolved_by: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class ModelVersion(Base):
    """Итерация дообучения (ТЗ 10, таблица ML_Retraining_Log; ТЗ 9.4)."""
    __tablename__ = "ml_retraining_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String, unique=True)
    dataset_version: Mapped[str] = mapped_column(String)
    matrix_version: Mapped[str] = mapped_column(String, default="")
    split_hashes: Mapped[dict] = mapped_column(JSON, default=dict)
    precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    f1: Mapped[float | None] = mapped_column(Float, nullable=True)
    false_positive_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    per_category_metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    training_params: Mapped[dict] = mapped_column(JSON, default=dict)
    code_ref: Mapped[str] = mapped_column(String, default="")
    previous_model: Mapped[str] = mapped_column(String, default="")
    acceptance: Mapped[dict] = mapped_column(JSON, default=dict)
    # PENDING → APPROVED | REJECTED; APPROVED → ROLLED_BACK при откате.
    approval_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    approved_by: Mapped[str] = mapped_column(String, default="")
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class WeeklyReport(Base):
    """Еженедельный отчёт для ML-инженеров (ТЗ 7, модуль 10)."""
    __tablename__ = "weekly_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_start: Mapped[dt.datetime] = mapped_column(DateTime)
    period_end: Mapped[dt.datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
