"""Разбор основной надписи по ГОСТ Р 21.1101 и определение вида документа."""
from __future__ import annotations

import re
from datetime import date, datetime

from documents.pdf import PageData
from documents.schemas import DocKind, Stamp, StateKind

RE_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
RE_REVISION = re.compile(r"Изм\.?\s*([0-9A-Za-zА-Яа-я]+)")
RE_STAGE = re.compile(r"Стади[яи]:?\s*([А-ЯA-Z])")
RE_CODE = re.compile(r"\b(\d{4}-\d{1,3}-[А-Яа-яA-Za-z]{2,4})\b")

# Определение вида документа по заголовку листа, а не по имени файла.
TITLE_MARKERS: list[tuple[str, DocKind, StateKind]] = [
    ("акт освидетельствования", DocKind.AOSR, StateKind.AS_BUILT),
    ("общий журнал работ", DocKind.JOURNAL, StateKind.AS_BUILT),
    ("паспорт качества", DocKind.PASSPORT, StateKind.AS_BUILT),
    ("протокол испытаний", DocKind.TEST_REPORT, StateKind.AS_BUILT),
    ("выписка из реестра", DocKind.SRO_EXTRACT, StateKind.AS_BUILT),
    ("заключение государственной экспертизы", DocKind.EXPERTISE, StateKind.EXPERTISE),
    ("рабочая документация", DocKind.RD, StateKind.RD),
    ("проектная документация", DocKind.PD, StateKind.PD),
    ("разрешение на строительство", DocKind.PERMIT, StateKind.PERMIT),
]


def _parse_date(text: str) -> date | None:
    m = RE_DATE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%d.%m.%Y").date()
    except ValueError:
        return None


def detect_kind(pages: list[PageData]) -> tuple[DocKind, StateKind]:
    """Вид документа по самому крупному заголовку первого листа."""
    if not pages:
        return DocKind.UNKNOWN, StateKind.AS_BUILT
    blocks = sorted(pages[0].blocks, key=lambda b: -b.size)[:6]
    for block in blocks:
        low = block.text.lower()
        for marker, kind, state in TITLE_MARKERS:
            if marker in low:
                return kind, state
    return DocKind.UNKNOWN, StateKind.AS_BUILT


def parse_stamp(pages: list[PageData]) -> Stamp:
    """Извлечь шифр, редакцию, стадию и дату из основной надписи.

    Основная надпись располагается в правой нижней четверти листа.
    """
    if not pages:
        return Stamp()
    page = pages[0]
    zone = [b for b in page.blocks
            if b.bbox[1] > page.height * 0.55 and b.bbox[2] > page.width * 0.35]
    text = " ".join(b.text for b in zone)
    stamp = Stamp()
    filled = 0
    if m := RE_CODE.search(text):
        stamp.code = m.group(1)
        filled += 1
    if m := RE_REVISION.search(text):
        stamp.revision = m.group(1)
        filled += 1
    if m := RE_STAGE.search(text):
        stamp.stage = m.group(1)
        filled += 1
    if d := _parse_date(text):
        stamp.doc_date = d
        filled += 1
    for block in zone:
        if block.text.startswith(("ООО", "АО", "ЗАО", "ГАУ", "ГБУ", "ПАО")):
            stamp.organization = block.text.split("ГИП")[0].strip()
            filled += 1
            break
    stamp.confidence = round(min(filled / 5, 1.0), 2)
    return stamp


def fallback_date(pages: list[PageData]) -> date | None:
    """Дата документа для форм без основной надписи (акты, паспорта, протоколы)."""
    for page in pages[:1]:
        for block in page.blocks[:12]:
            if d := _parse_date(block.text):
                return d
    return None
