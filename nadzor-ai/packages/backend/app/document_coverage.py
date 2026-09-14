"""Покрытие первичного разбора выбранных документов, независимо от раздела.

Успешное извлечение текста не означает успешную сверку или отсутствие
расхождений. Отчёт не запускает разбор и не угадывает стадию по стороне
сравнения: ПД, РД и ИД требуют отдельной идентификации документа.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .documents import DocumentFacts


class CoverageRequest(BaseModel):
    document_ids: list[int]


class CoveragePage(BaseModel):
    page: int
    status: Literal["processed", "unprocessed", "excluded"]
    text_status: Literal["available", "unavailable", "not_processed"]
    reason: str = ""


class CoverageDocument(BaseModel):
    document_id: int
    present: bool
    name: str = ""
    side: str | None = None
    discipline_code: str | None = None
    status: Literal["processed", "partial", "unprocessed", "error", "missing"]
    page_count: int | None = None
    pages: list[CoveragePage] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    scope: Literal["document_ingestion"] = "document_ingestion"
    documents: list[CoverageDocument]
    documents_requested: int
    documents_present: int
    documents_processed: int
    documents_partial: int
    documents_unprocessed: int
    documents_error: int
    documents_missing: int
    documents_unknown_page_count: int
    pages_known: int
    pages_processed: int
    pages_unprocessed: int
    pages_excluded: int
    pages_without_text: int
    ingestion_complete: bool
    text_extraction_complete: bool
    # Без ожидаемого состава нельзя установить, все ли нужные тома загружены.
    package_completeness: Literal["not_assessed"] = "not_assessed"
    comparison_status: Literal["not_assessed"] = "not_assessed"
    limitations: list[str] = Field(default_factory=lambda: [
        "Отчёт относится только к выбранным документам и первичному разбору, не к сверке.",
        "Ожидаемый состав комплекта не задан: отсутствие незагруженных томов не проверялось.",
        "Наличие текста не подтверждает чтение графики, извлечение всех требований "
        "или проверку ИИ.",
        "При сбое документа страницы без сохранённых фактов остаются необработанными; "
        "место сбоя неизвестно.",
    ])


class DocumentRecord(Protocol):
    id: int
    name: str
    side: str
    pages: int
    discipline_code: str | None
    status: str
    digest: str


def _document_coverage(document_id: int, doc: DocumentRecord | None,
                       read_facts: Callable[[str], DocumentFacts | None]) -> CoverageDocument:
    if doc is None:
        return CoverageDocument(document_id=document_id, present=False, status="missing",
                                reasons=["Запрошенный документ отсутствует в реестре."])

    facts = None
    reasons = []
    failed = doc.status == "error"
    if failed:
        reasons.append("Первичный разбор документа завершился ошибкой.")
    if doc.digest:
        try:
            facts = read_facts(doc.digest)
        except Exception:  # noqa: BLE001 — сбой одного кэша не скрывает остальные документы
            failed = True
            reasons.append("Сохранённый разбор недоступен или повреждён.")
    if facts is None:
        reasons.append("Нет сохранённых фактов актуальной версии разборщика.")

    count = doc.pages if doc.pages and doc.pages > 0 else None
    if facts is not None:
        if count is None and facts.pages > 0:
            count = facts.pages
        elif count != facts.pages:
            # Несогласованные числа нельзя молча привести к меньшему: так
            # исчезнет хвост страниц и получится ложное полное покрытие.
            failed = True
            reasons.append("Число страниц в реестре и сохранённом разборе не совпадает.")
    if count is None:
        reasons.append("Число страниц неизвестно.")

    kinds = facts.page_kinds if facts is not None else {}
    excluded = facts.excluded if facts is not None else {}
    text_pages = (
        {f["page"] for f in facts.text_facts if str(f.get("text") or "").strip()}
        if facts is not None else set()
    )
    pages = []
    for page in range(1, (count or 0) + 1):
        if page in excluded:
            row = CoveragePage(page=page, status="excluded", text_status="not_processed",
                               reason=excluded[page] or "Страница исключена разборщиком.")
        elif page not in kinds:
            row = CoveragePage(page=page, status="unprocessed", text_status="not_processed",
                               reason="Нет сохранённого результата разбора страницы.")
        else:
            has_text = page in text_pages
            row = CoveragePage(page=page, status="processed",
                               text_status="available" if has_text else "unavailable",
                               reason="" if has_text else
                               "Текст не извлечён; чтение изображения этим отчётом "
                               "не подтверждается.")
        pages.append(row)

    if failed:
        status = "error"
    elif doc.status != "ok":
        status = "unprocessed"
        reasons.append("Первичный разбор документа ещё не подтверждён завершённым.")
    elif pages and all(p.status == "processed" for p in pages):
        status = "processed"
    elif any(p.status != "unprocessed" for p in pages):
        status = "partial"
    else:
        status = "unprocessed"
    return CoverageDocument(document_id=document_id, present=True, name=doc.name,
                            side=doc.side, discipline_code=doc.discipline_code, status=status,
                            page_count=count, pages=pages, reasons=reasons)


def build_coverage(document_ids: list[int], documents: Mapping[int, DocumentRecord],
                   read_facts: Callable[[str], DocumentFacts | None]) -> CoverageReport:
    """Только имеющиеся свидетельства; загрузчик фактов не должен запускать разбор.

    ID, а не имя — идентификатор документа. Повтор одного ID не увеличивает
    покрытие; отсутствующий ID остаётся явной строкой отчёта.
    """
    rows = [_document_coverage(i, documents.get(i), read_facts)
            for i in dict.fromkeys(document_ids)]
    pages = [p for row in rows for p in row.pages]
    complete = bool(rows) and all(row.status == "processed" for row in rows)
    return CoverageReport(
        documents=rows,
        documents_requested=len(rows),
        documents_present=sum(row.present for row in rows),
        documents_processed=sum(row.status == "processed" for row in rows),
        documents_partial=sum(row.status == "partial" for row in rows),
        documents_unprocessed=sum(row.status == "unprocessed" for row in rows),
        documents_error=sum(row.status == "error" for row in rows),
        documents_missing=sum(row.status == "missing" for row in rows),
        documents_unknown_page_count=sum(row.page_count is None for row in rows),
        pages_known=len(pages),
        pages_processed=sum(p.status == "processed" for p in pages),
        pages_unprocessed=sum(p.status == "unprocessed" for p in pages),
        pages_excluded=sum(p.status == "excluded" for p in pages),
        pages_without_text=sum(p.text_status == "unavailable" for p in pages),
        ingestion_complete=complete,
        text_extraction_complete=complete and all(p.text_status == "available" for p in pages),
    )
