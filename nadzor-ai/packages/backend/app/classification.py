"""Определение раздела документа по шифру — из имени файла, титульного листа
и штампа чертежа.

Портировано из проверенной логики клиентского браузерного инструмента
(nadzor-browser/app.js: scanTextForDisciplineCodes/extractDisciplineCode),
с двумя добавлениями по итогам разбора реальных образцов документов:

1. На исполнительной документации штамп в правом нижнем углу листа часто —
   растровая картинка (флаттенированный скан/экспорт из CAD), а не текст:
   на реальном листе `АНО/150321/1-РД-ОВ1` PyMuPDF не находит в области
   штампа ни слова текстом — там 4 изображения. Поэтому для такого случая
   нужен запасной путь: распознавание кодового обозначения через
   vision-модель по вырезанному фрагменту штампа, а не по тексту.
2. На титульном листе тома шифр обычно присутствует прямым текстом (в
   отличие от штампа на листе чертежа) — поэтому первую страницу документа
   стоит проверять отдельно и с повышенным весом, до похода в штамп.

Обозначение раздела в конце шифра — это буквенный код (АР, ОВ, КР и т.п.),
поэтому сравниваем только его, отбрасывая последующие цифры тома/подраздела
(«ОВ2.1» и «ОВ1» — один и тот же раздел ОВ), как того требует оформление
шифров по ГОСТ Р 21.1101 (код раздела — всегда последний сегмент).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import pymupdf

def open_pdf(pdf_path: str) -> "pymupdf.Document":
    """Открывает PDF, снимая пустой пароль владельца, если он есть.

    Реальный случай из проверки на боевых документах: PDF-экспорты из
    проектных CAD-систем нередко зашифрованы пустым паролем владельца —
    это ограничивает копирование/печать в Acrobat, но не мешает открыть файл
    на просмотр там же. PyMuPDF же на таком файле отдаёт документ с
    недоступными страницами, если не аутентифицироваться явно, — без этого
    загрузка падала с невнятной ошибкой на листе, который на самом деле
    открывается и читается."""
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise ValueError("PDF защищён паролем, снять пустым паролем не удалось")
    return doc


DISCIPLINE_CODES = [
    "НВК", "ЭОМ", "АПС", "ОПС", "СКС", "ПОС",
    "КЖ", "КМ", "АР", "АС", "КР", "ОВ", "ВК", "ЭС", "СС", "ГП", "ТХ", "ПБ",
    # Г.63 — реально встреченные коды раздела: реестр «Состав документации»
    # (АНО/150321/1-РД-ОВ1, стр.10-12, найден composition_registry.py) даёт
    # 19 разных марок этого комплекта; выше уже было 18 обобщённых кодов
    # ГОСТ Р 21.1101, но ниже — то, чего там не было, а на реальном листе
    # есть: наружные/внутренние сети, тепловой пункт, слаботочка,
    # вертикальный транспорт. Статус n=1 (Г.21): подтверждено одним реальным
    # комплектом, не проверено на втором — короткие 2-буквенные коды («ОС»,
    # «ТС», «НС») теоретически рискуют шумом на другом корпусе текста, но
    # тот же риск уже принят для «АР»/«КР»/«ГП» выше.
    "НО", "НВ", "НК", "НС", "ВВ", "АУПТ", "ТС", "ИТП",
    "ЭЧ", "РФ", "ОС", "СОТ", "СКУД", "СКТВ", "СОУЭ", "ОЗДС", "АСУД", "ВТ", "НСС",
    # Г.79 — реальная находка: файлы раздела 8 по ПП№87 («Перечень
    # мероприятий по охране окружающей среды») маркированы составным кодом
    # «ООС» + номер тома («Том ООС8.4», «Том ООС8.3»), не буквенным «ОС»
    # (ГОСТ Р 21.1101, уже в списке выше) — два разных обозначения одного
    # раздела в разных нормативных системах, оба реально встречаются на
    # практике. Статус n=1 (Г.21): один реальный комплект, найдено при
    # ручном поиске тома ООС, не через classify_document — до этой правки
    # автоклассификация такие файлы не распознавала.
    "ООС",
]
DISCIPLINE_CODE_SET = set(DISCIPLINE_CODES)

_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-zА-Яа-яЁё0-9.\-]+")
_SEGMENT_SPLIT_RE = re.compile(r"[.\-]")
_TRAILING_DIGITS_RE = re.compile(r"\d+$")

# Г.80 — реальная находка: часть томов вообще не несёт буквенного кода в
# имени файла — например «V2_01-08-00-01-04_Том 8.1.pdf» (не «...ООС8.1»,
# как соседние тома 8.3/8.4 того же комплекта). Буквенный код там просто
# отсутствует, читать его неоткуда без открытия PDF. Но сама маркировка
# файла («01-08-00-01-04») несёт номер раздела ПРОЕКТНОЙ документации по
# Постановлению Правительства РФ №87 вторым числом — подтверждено на ВСЕХ
# 4 реальных файлах комплекта сразу (8.1/8.2/8.3/8.4 → везде «08»), не
# домыслено: «01-05-04-02-07_Том 5.4.2 ОВ» тоже даёт «05» вторым числом —
# раздел 5 (инженерное оборудование), что верно, просто раздел 5 сам
# делится на подсистемы (ОВ/ВК/ЭОМ/...) с разными буквенными кодами и
# поэтому НЕ включён в таблицу ниже (тот же принцип осторожности, что уже
# применён к 2-буквенным кодам в Г.63/Г.21 — не гадать там, где раздел
# неоднозначен). Таблица — только для разделов ПП№87, которые НЕ делятся
# на дисциплины внутри себя, поэтому номер раздела однозначно даёт код:
_SECTION_NUMBER_RE = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?!\d)")
_SECTION_NUMBER_CODES = {
    "02": "ГП",   # Схема планировочной организации земельного участка
    "03": "АР",   # Архитектурные решения
    "04": "КР",   # Конструктивные и объёмно-планировочные решения
    "06": "ПОС",  # Проект организации строительства
    "08": "ООС",  # Перечень мероприятий по охране окружающей среды
    "09": "ПБ",   # Мероприятия по обеспечению пожарной безопасности
}


def _section_number_code(text: str) -> Optional[str]:
    """Номер раздела ПП№87 — второе число в маркировке вида
    «NN-NN-NN-NN-NN» (проект-версия/раздел/подраздел/том/лист или похожая
    схема — сама структура наблюдалась по 5 реальным файлам, не домыслена).
    Возвращает код только для разделов из `_SECTION_NUMBER_CODES` — раздел
    5 (инженерное оборудование) намеренно не входит, там код зависит от
    подсистемы и без реального листа не определяется (Г.21/Г.63)."""
    m = _SECTION_NUMBER_RE.search(text or "")
    if not m:
        return None
    return _SECTION_NUMBER_CODES.get(m.group(2))

# Табличные графы штампа по ГОСТ Р 21.1101 — если они есть текстом на листе,
# это лист чертежа с текстовым (не растровым) штампом, а не титульный лист:
# сканировать его целиком на код раздела опасно (см. TITLE_PAGE_MAX_BLOCKS).
# «шифр» намеренно не входит сюда — оно легитимно встречается и на титульном
# листе тома, и по нему нельзя отличить штамп от титульника.
STAMP_KEYWORDS_RE = re.compile(r"стадия|гип\b|гап\b|изм\.|разраб", re.IGNORECASE)

MAX_STAMP_SCAN_PAGES = 5  # не сканируем текст штампа на всех 177 страницах тома
MIN_CODE_SCORE = 3        # порог уверенности — как в браузерном инструменте

# Настоящий титульный лист малонасыщен текстом (у реального тома ~22 текстовых
# блока); лист чертежа с экспликацией помещений — под сотни блоков (у
# реального листа — 476). Если блоков много, это не титульный лист, а
# содержательный чертёж, и сканировать его целиком на код раздела опасно:
# аббревиатуры категорий помещений («ВК», «В2» и т.п.) дают ложные совпадения.
TITLE_PAGE_MAX_BLOCKS = 60


def scan_text_for_discipline_codes(text: str) -> list[str]:
    found: list[str] = []
    for token in _TOKEN_SPLIT_RE.split(text or ""):
        if not token:
            continue
        segments = [s for s in _SEGMENT_SPLIT_RE.split(token) if s]
        for seg in reversed(segments):
            raw = seg.upper()
            core = _TRAILING_DIGITS_RE.sub("", raw)
            if core in DISCIPLINE_CODE_SET:
                found.append(core)
                break
            if raw in DISCIPLINE_CODE_SET:
                found.append(raw)
                break
    return found


# Между таблицей экспликации помещений (правый низ, но выше) и собственно
# штампом (самый угол) на реальных листах есть заметный зазор — узкая рамка
# для текстового скана держит совпадения только внутри настоящего штампа,
# не задевая соседнюю таблицу; для vision-вырезки берём с запасом шире, чтобы
# не обрезать штамп по компоновкам, отличным от увиденной в образцах.
def _stamp_text_scan_rect(page: "pymupdf.Page") -> "pymupdf.Rect":
    r = page.rect
    return pymupdf.Rect(r.width * 0.65, r.height * 0.90, r.width, r.height)


def _stamp_vision_crop_rect(page: "pymupdf.Page") -> "pymupdf.Rect":
    r = page.rect
    return pymupdf.Rect(r.width * 0.55, r.height * 0.82, r.width, r.height)


def _stamp_region_text(page: "pymupdf.Page") -> str:
    rect = _stamp_text_scan_rect(page)
    parts = []
    for b in page.get_text("blocks"):
        bx0, by0 = b[0], b[1]
        if bx0 >= rect.x0 and by0 >= rect.y0:
            parts.append(b[4])
    return " ".join(parts)


def render_stamp_crop_png(page: "pymupdf.Page", zoom: float = 2.0) -> bytes:
    """Растровая вырезка штампа для vision-fallback, когда текста там нет."""
    rect = _stamp_vision_crop_rect(page)
    pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
    return pix.tobytes("png")


@dataclass
class ClassificationResult:
    discipline_code: Optional[str]
    source: str  # 'filename' | 'filename_section_number' | 'title_page' | 'stamp_text' | 'stamp_vision' | 'none'
    scores: dict[str, int] = field(default_factory=dict)
    used_vision: bool = False


def classify_document(
    pdf_path: str,
    filename: str,
    vision_stamp_fn: Optional[Callable[[bytes], Optional[str]]] = None,
) -> ClassificationResult:
    """vision_stamp_fn(png_bytes) -> код раздела или None. Вызывается только
    если текстовых сигналов недостаточно — держит стоимость по vision-вызовам
    низкой (один на документ, а не на страницу)."""
    scores: dict[str, int] = {}

    def bump(code: str, weight: int) -> None:
        scores[code] = scores.get(code, 0) + weight

    for code in scan_text_for_discipline_codes(filename):
        bump(code, 3)
    best = _best_code(scores)
    if best is not None:
        return ClassificationResult(best, "filename", scores)

    # Г.80 — буквенный код в имени файла может отсутствовать вовсе (не
    # ошибка распознавания, а реальное отсутствие сигнала) — тогда пробуем
    # номер раздела ПП№87 из самой маркировки файла, для однозначных
    # разделов (см. докстринг `_section_number_code`).
    section_code = _section_number_code(filename)
    if section_code is not None:
        return ClassificationResult(section_code, "filename_section_number", {section_code: 3})

    doc = open_pdf(pdf_path)
    try:
        if doc.page_count == 0:
            return ClassificationResult(None, "none", scores)

        # Титульный лист: тот, что идёт до первого листа со штампом в правом
        # нижнем углу — на нём шифр обычно напечатан прямым текстом, это
        # самый дешёвый и надёжный сигнал после имени файла.
        first_page = doc[0]
        first_page_text = first_page.get_text("text")
        first_page_block_count = len(first_page.get_text("blocks"))
        is_plausible_title_page = (
            first_page_block_count <= TITLE_PAGE_MAX_BLOCKS
            and not STAMP_KEYWORDS_RE.search(first_page_text)
        )
        if is_plausible_title_page:
            for code in scan_text_for_discipline_codes(first_page_text):
                bump(code, 3)

        best = _best_code(scores)
        if best is not None:
            return ClassificationResult(best, "title_page", scores)

        # Штамп текстом — сканируем первые несколько листов, не весь том.
        for i in range(min(doc.page_count, MAX_STAMP_SCAN_PAGES)):
            stamp_text = _stamp_region_text(doc[i])
            for code in scan_text_for_discipline_codes(stamp_text):
                bump(code, 1)

        best = _best_code(scores)
        if best is not None:
            return ClassificationResult(best, "stamp_text", scores)

        # Штамп — картинка (типично для исполнительной документации, см.
        # докстринг модуля): без vision-модели код не прочитать.
        if vision_stamp_fn is not None:
            crop = render_stamp_crop_png(doc[0])
            vision_code = vision_stamp_fn(crop)
            if vision_code:
                code = vision_code.strip().upper()
                if code in DISCIPLINE_CODE_SET:
                    bump(code, 3)
                    return ClassificationResult(code, "stamp_vision", scores, used_vision=True)

        return ClassificationResult(None, "none", scores)
    finally:
        doc.close()


def _best_code(scores: dict[str, int]) -> Optional[str]:
    if not scores:
        return None
    code, score = max(scores.items(), key=lambda kv: kv[1])
    return code if score >= MIN_CODE_SCORE else None


# ---------- Тип листа: чертёж или текст/приложение ----------
# Объём ПД и РД/ИД почти никогда не совпадает даже внутри одного раздела —
# частая причина в том, что в один PDF подшиты вперемешку сами чертежи и
# текстовые приложения (акты, спецификации, содержание тома). Сравнивать
# чертёж с текстовым актом визуально бессмысленно — это разные типы листов,
# и их надо различать до сопоставления пар, а не только по разделу/шифру.
#
# Сигнал очень надёжный и не требует LLM: чертежи печатаются на крупном
# формате (А0-А3) и содержат десятки-сотни тысяч векторных линий (сам
# чертёж — это и есть векторная графика); текстовые листы почти всегда А4 и
# содержат от силы сотню линий (рамка таблицы, подпись). Разница на
# реальных образцах этой сессии — на три порядка (145 000+ против <100).
PAGE_KIND_DRAWING = "drawing"
PAGE_KIND_TEXT = "text"

DRAWING_FORMAT_LONG_SIDE_MM = 350.0  # больше А4 (297мм) — считаем чертёжным форматом
DRAWING_MIN_VECTOR_PATHS = 500


def classify_page_kind(page: "pymupdf.Page") -> str:
    rect = page.rect
    long_side_mm = max(rect.width, rect.height) * 25.4 / 72
    if long_side_mm >= DRAWING_FORMAT_LONG_SIDE_MM:
        return PAGE_KIND_DRAWING
    if len(page.get_drawings()) >= DRAWING_MIN_VECTOR_PATHS:
        return PAGE_KIND_DRAWING
    return PAGE_KIND_TEXT
