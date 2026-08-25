"""Извлечение постраничного текста из PDF для diff и сопоставления листов.

Порт извлечения текста из nadzor-browser/app.js (readPageItems/extractFileFacts)
на PyMuPDF. Разбор реестра помещений (roomFacts из экспликации) — отдельная,
более сложная регекс-логика в app.js; здесь пока не портирован (см. заметку в
документации по бэкенду) — text_facts достаточно для работы discipline-гейтинга
и diff, room_facts можно добавить отдельным шагом без изменения остального
пайплайна, так как matching.py/diffing.py уже рассчитаны на пустой список.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .classification import classify_page_kind


@dataclass
class DocumentFacts:
    name: str
    pages: int
    text_facts: list[dict]  # [{page, text}]
    room_facts: list[dict]  # [{page, key, name}] — пока всегда пусто, см. докстринг
    page_kinds: dict[int, str] = field(default_factory=dict)  # {page: 'drawing'|'text'}


def extract_document_facts(pdf_path: str, name: str) -> DocumentFacts:
    doc = pymupdf.open(pdf_path)
    try:
        text_facts = []
        page_kinds = {}
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text("text").strip()
            if text:
                text_facts.append({"page": i + 1, "text": text})
            page_kinds[i + 1] = classify_page_kind(page)
        return DocumentFacts(name=name, pages=doc.page_count, text_facts=text_facts, room_facts=[], page_kinds=page_kinds)
    finally:
        doc.close()
