"""Параметры/размеры по картинке — когда подпись в кривых (Г.30 п.2/п.3,
общий механизм). Аналог `balance_vision.py`, но привязан к ЛЮБОМУ якорю
(bbox), а не только к номеру помещения — чтобы работать и на листах без
экспликации, где единственный доступный якорь пришёл из `axes.py` или из
чистой координаты (`coord_registry.py`), а не из `rooms.py`.

Не зовётся из общего конвейера автоматически — по тем же причинам, что и
`balance_vision.py`: точечный, дорогой вызов на уже выбранного кандидата,
не сплошной проход по каждому листу."""
from __future__ import annotations

from typing import Optional

import pymupdf

from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url

RENDER_MARGIN = 300.0
RENDER_SCALE = 3.0

DIMENSION_OCR_PROMPT = """На картинке — фрагмент инженерного чертежа вокруг одного отмеченного
места (якоря — например номера помещения, оси или отметки высоты). Рядом
могут быть подписаны параметры: сечение воздуховода/канала («800х400»),
диаметр трубы («∅200»), мощность оборудования («Nэл.=34,0 кВт»), другие
числовые характеристики с единицей измерения.

Прочитай ТОЛЬКО то, что реально написано рядом с отмеченным местом на
картинке; не переноси подписи от других мест этой же картинки и ничего не
додумывай. Если рядом нет ни одной такой подписи — верни пустой список:
пустой результат лучше выдуманного (раздел 0, п.7).

Отвечай только JSON без пояснений вне JSON:
{"dimensions": [{"value": "800x400", "kind": "section или diameter или power или other"}],
 "found": true/false}"""


def render_anchor_crop_png(page: "pymupdf.Page", bbox: tuple[float, float, float, float],
                            margin: float = RENDER_MARGIN, scale: float = RENDER_SCALE) -> bytes:
    rect = page.rect
    x0, y0, x1, y1 = bbox
    clip = pymupdf.Rect(x0 - margin, y0 - margin, x1 + margin, y1 + margin) & rect
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip).tobytes("png")


def read_dimensions_vision(page: "pymupdf.Page", bbox: tuple[float, float, float, float],
                            anchor_label: str, config: LlmConfig) -> Optional[dict]:
    """Параметры/размеры у якоря `anchor_label`, чья зона на плане — `bbox`.
    None при сорванном вызове; `{"found": False, "dimensions": []}` —
    честный «рядом ничего нет», видимое состояние, не то же самое, что
    сбой (Г.10)."""
    try:
        png = render_anchor_crop_png(page, bbox)
    except Exception:  # noqa: BLE001 — сбой рендера одного кропа не должен ронять проверку остальных
        return None
    data_url = png_bytes_to_data_url(png)
    result = call_llm_json(config, DIMENSION_OCR_PROMPT,
                           f"Якорь: {anchor_label}. Прочитай параметры/размеры рядом с ним.",
                           images=[data_url])
    if not result:
        return None
    raw_dims = result.get("dimensions")
    dims: list[dict] = []
    if isinstance(raw_dims, list):
        for item in raw_dims:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                dims.append({"value": value.strip(), "kind": item.get("kind") or "other"})
    return {"anchor": anchor_label, "found": bool(result.get("found")), "dimensions": dims}
