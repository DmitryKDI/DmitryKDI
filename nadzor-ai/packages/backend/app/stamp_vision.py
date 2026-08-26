"""Распознавание основной надписи по картинке — когда штамп в кривых.

Зачем отдельно от stamp.py: у РД чертежи экспортированы из CAD с текстом,
переведённым в кривые, и штамп там текстом не читается вообще (замер:
69 страниц из 712, см. CLAUDE.md Г.6). Наименование листа — верхний уровень
сопоставления (Г.5), поэтому без распознавания маршрутизация РД не работает.

Распознаётся ТОЛЬКО угловой фрагмент, а не лист целиком: это на порядок
дешевле полного OCR и играет на сильную сторону моделей — весь бюджет
разрешения уходит на тот текст, который нужен. Материал векторный, поэтому
рендер фрагмента чёткий при любом увеличении, в отличие от фотоскана.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import pymupdf

from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url
from .stamp import Stamp

# Фрагмент штампа. Уже, чем зона текстового чтения в stamp.py: там запас
# нужен на таблицу изменений, здесь — наоборот, чем меньше лишнего, тем
# точнее распознавание.
CROP_LEFT = 0.72
CROP_TOP = 0.86
RENDER_SCALE = 3.0

STAMP_OCR_PROMPT = """На картинке — основная надпись (штамп) листа строительного чертежа
по ГОСТ Р 21.1101. Прочитай ТОЛЬКО то, что там написано; ничего не додумывай.

Поля:
- shifr — шифр проекта целиком, как написан (например «АНО/150321/1-РД-ОВ1»);
- sheet_no — число из графы «Лист» (не из «Листов» — это разные графы, в
  «Листов» стоит общее количество листов комплекта);
- sheet_name — наименование чертежа: «План подвала (вентиляция)»,
  «Принципиальная схема системы отопления (начало)» и т.п.;
- discipline_code — буквенный код раздела из хвоста шифра (ОВ, АР, КР, ЭОМ…).

Если поле неразличимо — верни для него null. Пустое поле лучше выдуманного.
Отвечай только JSON без пояснений вне JSON:
{"shifr": "…" или null, "sheet_no": <число> или null,
 "sheet_name": "…" или null, "discipline_code": "ОВ" или null}"""


def render_stamp_png(page: "pymupdf.Page", scale: float = RENDER_SCALE) -> bytes:
    rect = page.rect
    clip = pymupdf.Rect(rect.width * CROP_LEFT, rect.height * CROP_TOP,
                        rect.width, rect.height)
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")


def _as_int(value) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def read_stamp_ocr(page: "pymupdf.Page", config: LlmConfig) -> Stamp:
    """Штамп по картинке. Пустой Stamp, если модель не ответила или отказалась
    разбирать — выдуманное наименование хуже отсутствующего (раздел 0, п.7)."""
    data_url = png_bytes_to_data_url(render_stamp_png(page))
    result = call_llm_json(config, STAMP_OCR_PROMPT,
                           "Прочитай штамп.", images=[data_url])
    if not result:
        return Stamp()
    name = result.get("sheet_name")
    shifr = result.get("shifr")
    return Stamp(
        shifr=shifr.strip() if isinstance(shifr, str) and shifr.strip() else None,
        sheet_no=_as_int(result.get("sheet_no")),
        sheet_name=name.strip() if isinstance(name, str) and name.strip() else None,
    )
