"""Извлечение постраничного текста, штампа и реестра помещений из PDF для
diff и сопоставления листов.

Порт извлечения текста из nadzor-browser/app.js (readPageItems/extractFileFacts)
на PyMuPDF. Разбор реестра помещений — rooms.py, основная надпись — stamp.py,
отсев прайсов поставщика — material.py (у каждого свой докстринг).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .classification import classify_page_kind, open_pdf
from .material import non_project_reason
from .rooms import extract_room_facts
from .stamp import read_stamp


@dataclass
class DocumentFacts:
    name: str
    pages: int
    text_facts: list[dict]  # [{page, text}]
    room_facts: list[dict]  # [{page, key, name, area?}]
    page_kinds: dict[int, str] = field(default_factory=dict)  # {page: 'drawing'|'text'}
    # {page: {shifr, sheet_no, sheet_name}} — основная надпись, если читается текстом
    sheet_info: dict[int, dict] = field(default_factory=dict)
    # {page: причина} — лист исключён из сравнения как непроектный материал.
    # Видимое состояние, а не молчаливый пропуск: пользователь должен понимать,
    # почему по этому листу нет находок.
    excluded: dict[int, str] = field(default_factory=dict)


def extract_document_facts(pdf_path: str, name: str) -> DocumentFacts:
    doc = open_pdf(pdf_path)
    try:
        text_facts = []
        room_facts = []
        page_kinds = {}
        sheet_info = {}
        excluded = {}
        for i in range(doc.page_count):
            page = doc[i]
            page_no = i + 1
            text = page.get_text("text").strip()
            page_kinds[page_no] = classify_page_kind(page)

            reason = non_project_reason(text) if text else None
            if reason:
                excluded[page_no] = reason
                continue  # ни текста, ни помещений: материал не участвует в сравнении

            if text:
                text_facts.append({"page": page_no, "text": text})
                for fact in extract_room_facts(text):
                    room_facts.append({"page": page_no, **fact})

            stamp = read_stamp(page)
            if not stamp.is_empty():
                sheet_info[page_no] = {"shifr": stamp.shifr, "sheet_no": stamp.sheet_no,
                                       "sheet_name": stamp.sheet_name}
        return DocumentFacts(name=name, pages=doc.page_count, text_facts=text_facts,
                              room_facts=room_facts, page_kinds=page_kinds,
                              sheet_info=sheet_info, excluded=excluded)
    finally:
        doc.close()
