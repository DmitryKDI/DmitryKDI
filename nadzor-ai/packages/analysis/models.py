"""Модели анализа: комплект документов, находка, контекст запуска.

Контракт находки соответствует разделу 5.2 технического задания.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from documents.schemas import DocKind, Fact, StateKind
from pydantic import BaseModel, Field, model_validator

Severity = Literal["critical", "major", "minor"]

TRANSITIONS = {
    "T1": ("ПД ред. N → ПД ред. N+1", StateKind.PD, StateKind.PD),
    "T2": ("ПД → заключение экспертизы", StateKind.PD, StateKind.EXPERTISE),
    "T3": ("ПД → РД", StateKind.PD, StateKind.RD),
    "T4": ("РД ред. N → РД ред. N+1", StateKind.RD, StateKind.RD),
    "T5": ("РД → ИД", StateKind.RD, StateKind.AS_BUILT),
    "T6": ("ИД → ИД", StateKind.AS_BUILT, StateKind.AS_BUILT),
    "T7": ("любое состояние → нормативная база", None, None),
}


class ParsedDoc(BaseModel):
    """Разобранный документ с фактами."""
    id: str
    file_id: str
    object_id: str
    doc_kind: DocKind
    state_kind: StateKind
    title: str
    path: str = ""
    revision: str = "1"
    doc_date: date | None = None
    page_count: int = 1
    facts: list[Fact] = Field(default_factory=list)

    def facts_of(self, fact_type: str) -> list[Fact]:
        return [f for f in self.facts if f.fact_type == fact_type]

    def kv(self) -> dict[str, Fact]:
        """Реквизиты документа: канонический ключ → факт."""
        return {f.key: f for f in self.facts if f.fact_type == "kv"}

    def value(self, key: str, default: str = "") -> str:
        fact = self.kv().get(key)
        return str(fact.value) if fact else default


class DocumentSet(BaseModel):
    """Состояние жизненного цикла объекта — комплект документов одного вида."""
    object_id: str
    state_kind: StateKind
    revision: str = "1"
    label: str = ""
    docs: list[ParsedDoc] = Field(default_factory=list)

    def of_kind(self, *kinds: DocKind) -> list[ParsedDoc]:
        return [d for d in self.docs if d.doc_kind in kinds]

    def all_facts(self) -> list[Fact]:
        return [f for d in self.docs for f in d.facts]

    def doc_by_id(self, doc_id: str) -> ParsedDoc | None:
        return next((d for d in self.docs if d.id == doc_id), None)


class Evidence(BaseModel):
    """Доказательство с переходом к области на листе документа."""
    doc_id: str
    doc_title: str = ""
    page: int = 1
    bbox: tuple[float, float, float, float] = (0, 0, 1, 1)
    extracted: str = ""
    role: str = ""          # требование | факт | основание

    @classmethod
    def from_fact(cls, fact: Fact, doc: ParsedDoc, role: str, extracted: str = "") -> Evidence:
        return cls(doc_id=doc.id, doc_title=doc.title, page=fact.source.page,
                   bbox=fact.source.bbox, role=role,
                   extracted=extracted or _short(fact.value))


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Finding(BaseModel):
    """Гипотеза нарушения. Не заключение: юридической силы не имеет."""
    id: str
    public_id: str
    object_id: str
    transition: str
    code: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    priority_score: float = 0.0
    title: str
    structural_element: dict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1)
    norm_refs: list[dict] = Field(min_length=1)
    legal_refs: list[dict] = Field(default_factory=list)
    field_check: str = ""
    documents_to_request: list[str] = Field(default_factory=list)
    verification: dict = Field(default_factory=dict)
    injection_suspected: bool = False
    detector: str = "rule"
    provenance: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def _has_source(self) -> Finding:
        """Вывод без пункта норматива и координат в документе недопустим (Б.6)."""
        if not self.norm_refs:
            raise ValueError("находка без ссылки на пункт нормативного документа")
        if not any(e.bbox for e in self.evidence):
            raise ValueError("находка без координат области на листе документа")
        return self


@dataclass
class Detection:
    """Промежуточный результат правила или LLM-слоя до обогащения."""
    code: str
    title: str
    evidence: list[Evidence]
    element: dict = field(default_factory=dict)
    severity: Severity | None = None
    confidence: float = 0.95
    norm_refs: list[str] = field(default_factory=list)
    legal_refs: list[str] = field(default_factory=list)
    field_check: str = ""
    documents_to_request: list[str] = field(default_factory=list)
    detector: str = "rule"
    injection_suspected: bool = False


@dataclass
class AnalysisContext:
    """Контекст запуска анализа."""
    object_id: str
    run_id: str
    norms: Any
    rules_config: dict = field(default_factory=dict)
    scoring_config: dict = field(default_factory=dict)
    object_card: dict = field(default_factory=dict)
    provider: Any = None
    verifier: Any = None
    protected: bool = False
