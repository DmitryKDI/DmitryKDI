"""Схема хранения. Все запросы выполняются через ORM с параметризацией.

Конкатенация SQL в проекте запрещена и проверяется правилом линтера.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Кэш атрибутов сотрудника. Источник истины — СУДИР."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)       # subject из СУДИР
    full_name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[str] = mapped_column(String(200), default="")
    roles: Mapped[list] = mapped_column(JSON, default=list)
    valid_until: Mapped[str] = mapped_column(String(20), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ConstructionObject(Base):
    """Объект капитального строительства."""
    __tablename__ = "construction_objects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    permit_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    permit_date: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str] = mapped_column(Text)
    district: Mapped[str] = mapped_column(String(120), default="")
    cadastral_number: Mapped[str] = mapped_column(String(64), default="")
    developer: Mapped[str] = mapped_column(Text, default="")
    contractor: Mapped[str] = mapped_column(Text, default="")
    designer: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(Text, default="")
    planned_completion: Mapped[str] = mapped_column(String(20), default="")
    card: Mapped[dict] = mapped_column(JSON, default=dict)
    data_source: Mapped[str] = mapped_column(String(20), default="iais_ogd")
    department: Mapped[str] = mapped_column(String(200), index=True)
    assigned_to: Mapped[str] = mapped_column(String(64), index=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)

    documents: Mapped[list[DocumentRow]] = relationship(back_populates="object")


class DocumentRow(Base):
    """Принятый документ. Оригинал неизменяем."""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_id: Mapped[str] = mapped_column(ForeignKey("construction_objects.id"), index=True)
    state_kind: Mapped[str] = mapped_column(String(20), index=True)
    doc_kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[str] = mapped_column(String(20), default="1")
    doc_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    signature_status: Mapped[str] = mapped_column(String(20), default="not_checked")
    facts_count: Mapped[int] = mapped_column(Integer, default=0)

    object: Mapped[ConstructionObject] = relationship(back_populates="documents")


class AnalysisRun(Base):
    """Запуск анализа."""
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_id: Mapped[str] = mapped_column(ForeignKey("construction_objects.id"), index=True)
    transitions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(60), default="")
    started_by: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    documents_count: Mapped[int] = mapped_column(Integer, default=0)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)

    # Без relationship() unit of work не видит зависимость по FK и может
    # отправить INSERT analysis_runs раньше construction_objects в одном
    # flush — падает ForeignKeyViolationError при первом посеве демо-данных.
    object: Mapped[ConstructionObject] = relationship()


class FindingRow(Base):
    """Гипотеза нарушения."""
    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(20), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    object_id: Mapped[str] = mapped_column(ForeignKey("construction_objects.id"), index=True)
    transition: Mapped[str] = mapped_column(String(4), index=True)
    code: Mapped[str] = mapped_column(String(8), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    priority_score: Mapped[float] = mapped_column(Float, index=True)
    title: Mapped[str] = mapped_column(Text)
    structural_element: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    norm_refs: Mapped[list] = mapped_column(JSON, default=list)
    legal_refs: Mapped[list] = mapped_column(JSON, default=list)
    field_check: Mapped[str] = mapped_column(Text, default="")
    documents_to_request: Mapped[list] = mapped_column(JSON, default=list)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    injection_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    detector: Mapped[str] = mapped_column(String(60), default="rule")
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Та же причина, что у AnalysisRun.object выше: без relationship()
    # порядок flush между findings, analysis_runs и construction_objects
    # не гарантирован.
    run: Mapped[AnalysisRun] = relationship()
    object: Mapped[ConstructionObject] = relationship()


class Assessment(Base):
    """Оценка гипотезы инспектором."""
    __tablename__ = "finding_assessments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("findings.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    photos: Mapped[list] = mapped_column(JSON, default=list)
    user_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    finding: Mapped[FindingRow] = relationship()


class AuditRow(Base):
    """Запись журнала аудита. Только добавление."""
    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occurred_at: Mapped[str] = mapped_column(String(40))
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_role: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), unique=True)
