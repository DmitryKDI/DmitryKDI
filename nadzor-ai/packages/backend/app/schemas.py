from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    side: str
    pages: int
    discipline_code: str | None
    classification_source: str | None
    status: str
    uploaded_at: dt.datetime


class AnalysisRunCreate(BaseModel):
    before_document_ids: list[int]
    after_document_ids: list[int]


class AnalysisRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime
    status: str
    pairs_total: int
    pairs_done: int
    provider: str
    model: str
    pairs_llm_ok: int
    pairs_llm_error: int
    error: str | None


class PagePairOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    before_document_id: int
    before_page: int
    after_document_id: int
    after_page: int
    matched_by: str
    page_kind: str
    discipline_mismatch: bool
    llm_status: str
    llm_error: str | None


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    pair_id: int | None
    kind: str
    label: str
    change_text: str
    severity: str
    field_check: str
    reviewed_status: str
    created_at: dt.datetime
    before_document_id: int | None
    before_page: int | None
    after_document_id: int | None
    after_page: int | None


class FindingUpdate(BaseModel):
    reviewed_status: str


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    base_url: str
    model: str
    api_key: str


class SettingsUpdate(BaseModel):
    provider: str
    base_url: str = ""
    model: str = ""
    api_key: str = ""
