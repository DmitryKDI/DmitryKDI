"""Рендер листа PDF в картинку и vision-сравнение пары листов — плюс
vision-чтение штампа, когда там нет текстового слоя (см. classification.py).

Порт renderPageToImage/callVisionLlm/AI_VISION_SYSTEM_PROMPT из
nadzor-browser/main.js на PyMuPDF + llm.py.
"""
from __future__ import annotations

from typing import Optional

import pymupdf

from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url

VISION_MAX_DIM = 1600

VISION_SYSTEM_PROMPT = """Ты проверяешь строительную документацию. Тебе показаны две картинки одного
листа: слева — из проектной документации (ПД), справа — из рабочей или
исполнительной (РД/ИД). Найди только содержательные расхождения — то, что
реально может быть нарушением или требует проверки инспектором (изменение
размеров, материалов, положения элементов, отсутствие/добавление элемента).
НЕ упоминай различия в качестве скана, повороте, обрезке, цвете фона,
нумерации листов и другие технические артефакты не по существу.
Отвечай только JSON без пояснений вне JSON:
{"significant": [{"label": "краткий код", "change": "что изменилось и почему это важно"}],
 "noise_note": "что отброшено как несущественное, кратко",
 "checked_total": <int>, "significant_total": <int>}"""

STAMP_READ_SYSTEM_PROMPT = """На картинке — угловой штамп листа строительного чертежа (ГОСТ Р 21.1101).
Прочитай шифр проекта и определи код раздела — двух-четырёхбуквенное
обозначение в конце шифра (АР, КР, ОВ, ВК, ЭОМ и т.п.). Если код раздела
неразличим или отсутствует — верни null.
Отвечай только JSON без пояснений вне JSON:
{"discipline_code": "ОВ" или null, "sheet_name": "наименование чертежа с листа, если видно"}"""


def render_page_to_data_url(pdf_path: str, page_no: int, max_dim: int = VISION_MAX_DIM) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        rect = page.rect
        scale = max_dim / max(rect.width, rect.height)
        scale = min(scale, 4.0)  # не апскейлим совсем маленькие страницы сверх разумного
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return png_bytes_to_data_url(pix.tobytes("png"))
    finally:
        doc.close()


def make_llm_stamp_classifier(config: LlmConfig):
    """Возвращает функцию, совместимую с classification.classify_document's
    vision_stamp_fn: принимает PNG-байты штампа, возвращает код раздела."""

    def classify(png_bytes: bytes) -> Optional[str]:
        data_url = png_bytes_to_data_url(png_bytes)
        result = call_llm_json(config, STAMP_READ_SYSTEM_PROMPT, "Определи раздел по штампу.", images=[data_url])
        if not result:
            return None
        code = result.get("discipline_code")
        return code if code else None

    return classify


def compare_page_pair(
    before_pdf: str,
    before_page: int,
    after_pdf: str,
    after_page: int,
    config: LlmConfig,
    context: str = "",
) -> Optional[dict]:
    before_img = render_page_to_data_url(before_pdf, before_page)
    after_img = render_page_to_data_url(after_pdf, after_page)
    user_text = "Сравни левый лист (ПД) и правый лист (РД/ИД)."
    if context:
        user_text += f" Контекст: {context}."
    return call_llm_json(config, VISION_SYSTEM_PROMPT, user_text, images=[before_img, after_img])
