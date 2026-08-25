"""Извлечение постраничного текста и реестра помещений из PDF для diff и
сопоставления листов.

Порт извлечения текста из nadzor-browser/app.js (readPageItems/extractFileFacts)
на PyMuPDF. Разбор реестра помещений — rooms.py (см. его докстринг).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .classification import classify_page_kind, open_pdf
from .rooms import extract_room_facts


@dataclass
class DocumentFacts:
    name: str
    pages: int
    text_facts: list[dict]  # [{page, text}]
    room_facts: list[dict]  # [{page, key, name}]
    page_kinds: dict[int, str] = field(default_factory=dict)  # {page: 'drawing'|'text'}


def extract_document_facts(pdf_path: str, name: str) -> DocumentFacts:
    doc = open_pdf(pdf_path)
    try:
        text_facts = []
        room_facts = []
        page_kinds = {}
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text("text").strip()
            if text:
                text_facts.append({"page": i + 1, "text": text})
                for fact in extract_room_facts(text):
                    room_facts.append({"page": i + 1, **fact})
            page_kinds[i + 1] = classify_page_kind(page)
        return DocumentFacts(name=name, pages=doc.page_count, text_facts=text_facts,
                              room_facts=room_facts, page_kinds=page_kinds)
    finally:
        doc.close()
