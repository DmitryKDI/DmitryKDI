"""Номера помещений на листе — из «Экспликации помещений» (табличная форма)
или из подписи у контура помещения на самом плане (инженерная схема).

Порт по смыслу RE_ROOM_ROW/RE_ROOM_LIST_ROW из packages/documents/extract.py:
там ожидается одна строка «номер название площадь», собранная детектором
таблиц полной CRM. Здесь источник — плоский page.get_text('text') из
PyMuPDF, где на реальных документах номер, название и площадь почти всегда
разнесены PyMuPDF по отдельным строкам (проверено на настоящих файлах
Nadzor_Sample), поэтому строки сначала схлопываются в логическую запись.
"""
from __future__ import annotations

import re

_ROOM_NO_RE = re.compile(r"^(\d{3,4}(?:[.,]\d{1,2})?[а-яё]?)$")
_AREA_RE = re.compile(r"^\d+[.,]\d+$")
_CATEGORY_RE = re.compile(r"^[АВ]\d?$")
_NAME_START_RE = re.compile(r"^[А-ЯЁ]")
# Форма "002 Коридор" — номер и название на одной строке (короткие подписи на
# плане чаще попадают в один текстовый блок, чем строки таблицы).
_INLINE_ROW_RE = re.compile(
    r"^(\d{3,4}(?:[.,]\d{1,2})?[а-яё]?)\s+([А-ЯЁ][^\d\n]{1,90}?)(?:\s+(\d+[.,]\d+))?(?:\s+([АВ]\d?))?$"
)
_MAX_NAME_LINES = 3


def _plausible_name(name: str) -> bool:
    """Одна заглавная буква ("А", "Р") — почти всегда обрывок обозначения оси
    или марки, случайно совпавший с началом названия, а не реальное имя
    помещения, поэтому это лучше отбросить, чем засорить реестр шумом."""
    return len(name) >= 3


def extract_room_facts(text: str) -> list[dict]:
    """Возвращает [{key, name}] — номера помещений, найденные на листе.
    Ложные срабатывания отсекаются требованием: после номера обязательно
    идёт название, начинающееся с заглавной кириллической буквы — просто
    число (отметка, номер оси, размер) без такого продолжения не попадает."""
    lines = [ln.strip() for ln in text.splitlines()]
    facts: list[dict] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue

        m_number = _ROOM_NO_RE.match(line)
        if m_number:
            key = m_number.group(1)
            j = i + 1
            name_parts: list[str] = []
            while j < n and len(name_parts) < _MAX_NAME_LINES:
                frag = lines[j].strip()
                if not frag:
                    j += 1
                    continue
                if _ROOM_NO_RE.match(frag):
                    break  # следующая запись реестра началась
                if _AREA_RE.match(frag):
                    j += 1
                    break  # площадь — конец текущей записи
                if _CATEGORY_RE.match(frag) and name_parts:
                    j += 1
                    break  # категория помещения после названия — тоже конец
                if not _NAME_START_RE.match(frag):
                    break  # не похоже на продолжение названия
                name_parts.append(frag)
                j += 1
            name = " ".join(name_parts)
            if _plausible_name(name):
                facts.append({"key": key, "name": name})
            i = j
            continue

        m_inline = _INLINE_ROW_RE.match(line)
        if m_inline and _plausible_name(m_inline.group(2).strip()):
            facts.append({"key": m_inline.group(1), "name": m_inline.group(2).strip()})
        i += 1

    return facts
