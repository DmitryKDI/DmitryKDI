"""Схемы приёма документов и извлечённых фактов.

Ключевое требование: каждый факт несёт source_ref — файл, лист и координаты
области на листе. Факт без source_ref в систему не попадает.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class StateKind(str, Enum):
    """Состояние жизненного цикла объекта."""
    PD = "PD"                # проектная документация
    RD = "RD"                # рабочая документация
    EXPERTISE = "EXPERTISE"  # заключение экспертизы
    AS_BUILT = "AS_BUILT"    # исполнительная документация
    PERMIT = "PERMIT"        # разрешительные документы


class DocKind(str, Enum):
    """Вид документа внутри состояния."""
    PD = "pd"
    RD = "rd"
    EXPERTISE = "expertise"
    AOSR = "aosr"
    JOURNAL = "journal"
    PASSPORT = "passport"
    TEST_REPORT = "test_report"
    SRO_EXTRACT = "sro_extract"
    PERMIT = "permit"
    UNKNOWN = "unknown"


class SourceRef(BaseModel):
    """Координаты факта в исходном документе."""
    file_id: str
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]

    @field_validator("bbox")
    @classmethod
    def _valid_bbox(cls, v: tuple[float, float, float, float]):
        x0, y0, x1, y1 = v
        if x1 <= x0 or y1 <= y0:
            raise ValueError("вырожденная область на листе")
        return v


class Fact(BaseModel):
    """Извлечённая сущность. Именно факты, а не сырой текст, идут в LLM-слой."""
    id: str
    doc_id: str
    fact_type: str            # kv | element | attachment | text | stamp
    key: str = ""
    value: Any = None
    section: str = ""
    source: SourceRef
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Stamp(BaseModel):
    """Основная надпись по ГОСТ Р 21.1101."""
    code: str = ""
    revision: str = ""
    stage: str = ""
    doc_date: date | None = None
    organization: str = ""
    section: str = ""
    confidence: float = 0.0


class DocumentMeta(BaseModel):
    """Карточка принятого документа."""
    id: str
    object_id: str
    state_kind: StateKind
    doc_kind: DocKind
    title: str
    original_name: str
    storage_key: str
    sha256: str
    detected_mime: str
    size_bytes: int
    page_count: int = 0
    revision: str = "1"
    doc_date: date | None = None
    signature_status: str = "not_checked"
    av_status: str = "skipped"
    injection_suspected: bool = False


class IntakeError(Exception):
    """Отказ в приёме файла. Сообщение показывается пользователю как есть."""
