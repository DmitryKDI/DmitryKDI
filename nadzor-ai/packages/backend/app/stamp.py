"""Основная надпись листа по ГОСТ Р 21.1101 — шифр, номер листа, наименование.

Штамп отвечает на вопрос «что это за лист» напрямую, а не через догадку по
совпадению слов, и потому обязан читаться первым (см. CLAUDE.md, Г.5):
«Принципиальная схема системы отопления (начало)» ищет пару среди листов
отопления, а не среди всех 712 листов комплекта.

Проверено на реальных документах: у ПД штамп читается текстом целиком, у РД
он экспортирован в кривые — там наименование доступно только через
распознавание углового фрагмента (vision.make_llm_stamp_classifier).
Поэтому функции ниже возвращают None честно, а не выдумывают наименование:
пустой результат — сигнал «нужно распознавание», а не «лист без имени».
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import pymupdf

# Доля листа, занятая основной надписью: правый нижний угол. Взято с запасом
# вверх, потому что над штампом у части листов идёт таблица изменений.
STAMP_LEFT = 0.55
STAMP_TOP = 0.60

_SHIFR_RE = re.compile(r"[A-ZА-Я]{2,}[/\-]\d{4,}[/\-]\d+[-–]([А-Яа-яA-Za-z0-9.\-]+)")
# Служебные графы штампа — это не наименование листа.
_STAMP_LABELS = re.compile(
    r"Изм\.|Кол\.|Стадия|Подпись|Дата|ГИП|ГАП|ООО|Формат|Разраб|Провер|"
    r"Н\.контр|Листов|№\s*док|Согласовано|Взам\.|инв\.|подл\.", re.I)


@dataclass
class Stamp:
    shifr: Optional[str] = None        # хвост шифра: П-ИОС5.4.2, РД-ОВ1, П-ВОР.ИОС5.4.2
    sheet_no: Optional[int] = None     # номер листа из графы «Лист»
    sheet_name: Optional[str] = None   # наименование чертежа

    def is_empty(self) -> bool:
        return self.shifr is None and self.sheet_no is None and self.sheet_name is None


def _sheet_number(words: list) -> Optional[int]:
    """Номер листа — в графе «Лист», зажатой между «Стадия» и «Листов».

    Слово «Лист» встречается в штампе трижды (таблица изменений, графа
    «Лист» основной надписи, «Листов»), поэтому опознаётся именно по
    соседям, а не по первому совпадению."""
    stadia = [w for w in words if w[4] == "Стадия"]
    listov = [w for w in words if w[4] == "Листов"]
    if not stadia or not listov:
        return None
    for label in [w for w in words if w[4] == "Лист"]:
        if not stadia[0][0] < label[0] < listov[0][0]:
            continue
        below = [w for w in words if re.fullmatch(r"\d{1,3}", w[4])
                 and abs(w[0] - label[0]) < 25 and label[3] < w[1] < label[3] + 45]
        if below:
            return int(below[0][4])
    return None


def read_stamp(page: "pymupdf.Page") -> Stamp:
    rect = page.rect
    clip = pymupdf.Rect(rect.width * STAMP_LEFT, rect.height * STAMP_TOP,
                        rect.width, rect.height)
    text = page.get_text("text", clip=clip)
    if not text.strip():
        return Stamp()  # штамп в кривых — нужен vision, см. докстринг модуля

    m = _SHIFR_RE.search(text.replace("\n", " "))
    lines = [ln.strip() for ln in text.splitlines()
             if len(ln.strip()) > 10 and not _STAMP_LABELS.search(ln) and not _SHIFR_RE.search(ln)]
    return Stamp(
        shifr=m.group(1).strip(" .") if m else None,
        sheet_no=_sheet_number(page.get_text("words", clip=clip)),
        sheet_name=" ".join(lines[:2])[:120] if lines else None,
    )
