"""Баланс притока/вытяжки по картинке — когда рамка в кривых (Г.30, п.1).

Основной путь для этого типа факта на реальных комплектах: прямой замер
показал, что баланс-рамка почти никогда не попадает в текстовый слой листа
схемы (Г.13, CAD-текст в кривых) — в отличие от штампа, для неё нет
единого фиксированного места на листе (штамп всегда в углу, баланс-рамка —
у каждого номера помещения свой). Поэтому, в отличие от `stamp_vision.py`,
зона рендера строится не долями страницы, а вокруг найденного на плане
номера помещения.

Кто зовёт `read_balance_box_vision` — не автоматический шаг основного
конвейера `documents.py` (тот остаётся без LLM), а явный, точечный вызов
— по образцу `verify_candidate` в `registry_diff.py`: для конкретного
кандидата, не для всего листа сразу."""
from __future__ import annotations

from typing import Optional

import pymupdf

from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url

RENDER_MARGIN = 300.0
RENDER_SCALE = 3.0

BALANCE_OCR_PROMPT = """На картинке — фрагмент инженерной схемы (вентиляция/отопление) вокруг
номера одного помещения (он в кружке). Рядом с номером обычно стоит
рамка/группа подписей: код системы (например «П2/ВЕ»), и одно или два
числа со знаком «+» (приток) и «−»/«-» (вытяжка) с единицей «м³/ч» после
каждого. Прочитай ТОЛЬКО то, что реально написано на картинке рядом с этим
номером помещения; ничего не додумывай и не переноси данные от других
помещений на этой же картинке.

Если рамки баланса нет вообще (просто графика, без такой подписи) — верни
null для всех полей: пустой результат лучше выдуманного (раздел 0, п.7).

Отвечай только JSON без пояснений вне JSON:
{"system_code": "…" или null, "pritok_m3ch": <число> или null,
 "vytyazhka_m3ch": <число> или null, "found": true/false}"""


def find_room_label_bboxes(page: "pymupdf.Page", room_key: str) -> list[tuple[float, float, float, float]]:
    """Все места на листе, где номер помещения встречается как отдельное
    слово (не часть строки экспликации) — кандидаты в «номер на плане».
    Возвращает все совпадения; какое из них реально на графике, а какое —
    в таблице экспликации, решает вызывающий код (или сам факт того, что
    зрение не нашло рамку рядом — сигнал "не то место", не ошибка)."""
    return [tuple(w[:4]) for w in page.get_text("words") if w[4] == room_key]


def render_balance_crop_png(page: "pymupdf.Page", bbox: tuple[float, float, float, float],
                             margin: float = RENDER_MARGIN, scale: float = RENDER_SCALE) -> bytes:
    rect = page.rect
    x0, y0, x1, y1 = bbox
    clip = pymupdf.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin) & rect
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")


def _as_number(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return None


def read_balance_box_vision(page: "pymupdf.Page", bbox: tuple[float, float, float, float],
                             room_key: str, config: LlmConfig) -> Optional[dict]:
    """Баланс притока/вытяжки у номера помещения `room_key`, чей якорь на
    плане лежит в `bbox` — по картинке. None, если зона не отрендерилась
    или модель не ответила; `{"found": False}`-подобный результат от
    модели (рамки нет на этом кропе) возвращается как есть — это тоже
    видимое состояние (Г.10), не то же самое, что сбой вызова."""
    try:
        png = render_balance_crop_png(page, bbox)
    except Exception:  # noqa: BLE001 — сбой рендера одного кропа не должен ронять проверку остальных
        return None
    data_url = png_bytes_to_data_url(png)
    result = call_llm_json(config, BALANCE_OCR_PROMPT,
                           f"Помещение {room_key}. Прочитай баланс притока/вытяжки рядом с его номером.",
                           images=[data_url])
    if not result:
        return None
    fact: dict = {"room_key": room_key, "found": bool(result.get("found"))}
    system_code = result.get("system_code")
    if isinstance(system_code, str) and system_code.strip():
        fact["system_code"] = system_code.strip()
    pritok = _as_number(result.get("pritok_m3ch"))
    if pritok:
        fact["приток_м3ч"] = pritok
    vytyazhka = _as_number(result.get("vytyazhka_m3ch"))
    if vytyazhka:
        fact["вытяжка_м3ч"] = vytyazhka
    return fact
