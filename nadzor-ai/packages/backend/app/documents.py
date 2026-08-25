"""Извлечение постраничного текста из PDF для diff и сопоставления листов.

Порт извлечения текста из nadzor-browser/app.js (readPageItems/extractFileFacts)
на PyMuPDF. Разбор реестра помещений (roomFacts из экспликации) — отдельная,
более сложная регекс-логика в app.js; здесь пока не портирован (см. заметку в
документации по бэкенду) — text_facts достаточно для работы discipline-гейтинга
и diff, room_facts можно добавить отдельным шагом без изменения остального
пайплайна, так как matching.py/diffing.py уже рассчитаны на пустой список.
"""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass
class DocumentFacts:
    name: str
    pages: int
    text_facts: list[dict]  # [{page, text}]
    room_facts: list[dict]  # [{page, key, name}] — пока всегда пусто, см. докстринг


def extract_document_facts(pdf_path: str, name: str) -> DocumentFacts:
    doc = pymupdf.open(pdf_path)
    try:
        text_facts = []
        for i in range(doc.page_count):
            text = doc[i].get_text("text").strip()
            if text:
                text_facts.append({"page": i + 1, "text": text})
        return DocumentFacts(name=name, pages=doc.page_count, text_facts=text_facts, room_facts=[])
    finally:
        doc.close()
