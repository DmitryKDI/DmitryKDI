"""Сравнение пары листов — по картинке для чертежей, по тексту для текстовых
листов/приложений (см. classification.classify_page_kind — почему это два
разных пути, а не один). Плюс рендер листа в картинку и vision-чтение
штампа, когда там нет текстового слоя (см. classification.py).

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
исполнительной (РД/ИД). Раздел уже определён отдельно и передан тебе в
контексте — сверять код в углу листа не нужно, смотри только на содержимое
самого чертежа/плана: расположение и размеры элементов, материалы,
конструктивные решения, состав помещений, инженерное оборудование.

Для каждого значимого расхождения обязательно укажи, ГДЕ оно на листе —
координатный ориентир (оси, номер помещения, зона листа: например «между
осями 3-5», «санузел 214», «верхний правый угол»), а не только что
изменилось. Расхождение без ориентира на листе для инспектора бесполезно.

Значимо — то, что реально может быть нарушением или требует проверки на
объекте: другой размер/материал/класс, элемент появился или исчез, другое
положение. Пример значимого: "балка Б-3 у оси В смещена на ~600мм к оси Г".
НЕ значимо и не включай: качество скана, поворот, обрезка, цвет фона,
почерк подписи, нумерация листов, различия в оформлении рамки/шрифта.
Пример НЕ значимого: "на правом листе текст чуть темнее".

Отвечай только JSON без пояснений вне JSON:
{"significant": [{"label": "краткий код", "change": "что изменилось, где на листе и почему это важно"}],
 "noise_note": "что отброшено как несущественное, кратко",
 "checked_total": <int>, "significant_total": <int>}"""

TEXT_COMPARE_SYSTEM_PROMPT = """Ты проверяешь строительную документацию. Тебе показан текст одного и того
же листа из двух комплектов: ПД (проектная) и РД/ИД (рабочая или
исполнительная) — акт, спецификация, содержание тома или другой текстовый
лист (не чертёж).

Найди только содержательные расхождения — то, что реально может быть
нарушением или требует проверки: другое значение (размер, марка, класс
материала, количество, дата, срок), другой пункт/раздел, появившийся или
пропавший абзац/позиция. Не считай расхождением: перенумерацию позиций в
перечне без изменения содержания, форматирование, порядок слов без
изменения смысла, различия в пробелах/переносах строк.

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


def render_page_to_png_bytes(pdf_path: str, page_no: int, max_dim: int = VISION_MAX_DIM) -> bytes:
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        rect = page.rect
        scale = max_dim / max(rect.width, rect.height)
        scale = min(scale, 4.0)  # не апскейлим совсем маленькие страницы сверх разумного
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return pix.tobytes("png")
    finally:
        doc.close()


def render_page_to_data_url(pdf_path: str, page_no: int, max_dim: int = VISION_MAX_DIM) -> str:
    return png_bytes_to_data_url(render_page_to_png_bytes(pdf_path, page_no, max_dim))


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


def compare_text_pair(
    before_text: str,
    after_text: str,
    config: LlmConfig,
    context: str = "",
) -> Optional[dict]:
    user_text = f"ПД:\n{before_text[:8000]}\n\nРД/ИД:\n{after_text[:8000]}"
    if context:
        user_text = f"Контекст: {context}.\n\n{user_text}"
    return call_llm_json(config, TEXT_COMPARE_SYSTEM_PROMPT, user_text)
