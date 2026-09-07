"""Состав тома рабочей документации (Г.95).

Прямое указание пользователя: «если в РД нет текста для сводки, то просто
он должен говорить, что в документе столько листов графической части,
столько спецификаций и т.д.». Это не запасной вариант на случай неудачи, а
самостоятельный результат: у РД текстового слоя почти нет по природе
(CAD-экспорт переводит подписи в кривые, Г.8/Г.59), и «сводка пустая» без
объяснения выглядело бы как сбой.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from app.document_composition import describe_volume, render_composition  # noqa: E402

# Кириллица требует шрифта с её поддержкой: встроенный helv её не кодирует
# и `get_text` возвращает точки вместо букв — синтетика молча получалась бы
# нечитаемой, а тест «не находит таблицу» указывал бы на код вместо фикстуры.
CYRILLIC_TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _pdf(path: Path, pages: list[tuple[str, tuple[float, float]]],
         stamp: str | None = None) -> str:
    """Страницы как (текст, размер листа в пунктах).

    `stamp` кладётся в ПРАВЫЙ НИЖНИЙ угол — туда, где штамп ищет `read_stamp`
    (ГОСТ Р 21.1101). Текст в теле листа туда не попадает, и фикстура со
    штампом вверху молча проверяла бы не то, что написано в названии теста.
    """
    doc = pymupdf.open()
    font = pymupdf.Font(fontfile=CYRILLIC_TTF)
    for text, size in pages:
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_font(fontname="F0", fontbuffer=font.buffer)
        if stamp:
            page.insert_textbox(
                pymupdf.Rect(size[0] * 0.57, size[1] * 0.62, size[0] - 10, size[1] - 10),
                stamp, fontname="F0", fontsize=9)
        if text:
            # insert_textbox переносит строки внутри рамки. Через insert_text
            # длинный текст молча уезжал за край листа и не извлекался — тест
            # тогда проверял не то, что написано в его названии.
            page.insert_textbox(pymupdf.Rect(36, 36, size[0] - 36, size[1] - 36),
                                text, fontname="F0", fontsize=9)
    doc.save(str(path))
    doc.close()
    return str(path)


A4 = (595, 842)
A1_LANDSCAPE = (2384, 1684)  # длинная сторона ~841 мм — формат чертежа


def test_counts_drawings_and_text_sheets_separately(tmp_path):
    """Инспектору нужно понимать объём: чертежи и текстовые листы — разные
    виды работы и разная стоимость проверки."""
    path = _pdf(tmp_path / "rd.pdf", [
        ("Пояснительная записка. " * 40, A4),
        ("", A1_LANDSCAPE),
        ("", A1_LANDSCAPE),
    ])
    c = describe_volume(path, "rd.pdf")

    assert c.pages == 3
    assert c.drawing_pages == 2
    assert c.text_pages == 1


def test_empty_text_layer_is_reported_as_its_own_state(tmp_path):
    """Г.8 — лист с пустым текстовым слоем не «пустой лист»: это CAD-экспорт
    в кривые. Пропустить это молча значит выдать «текста нет» за «в томе
    ничего нет», чего Г.10 не допускает."""
    path = _pdf(tmp_path / "rd.pdf", [("", A1_LANDSCAPE), ("", A1_LANDSCAPE)])
    c = describe_volume(path, "rd.pdf")

    assert c.pages_without_text == 2
    text = render_composition([c])
    assert "без текстового слоя" in text


def test_known_tables_are_counted_by_type(tmp_path):
    """Спецификации и прочие таблицы считаются по типам из реестра
    (`table_registry`), а не одним числом: «12 таблиц» инспектору ничего не
    говорит, «2 спецификации оборудования» — говорит."""
    path = _pdf(tmp_path / "rd.pdf", [
        ("Спецификация оборудования, изделий и материалов", A4),
        ("Ведомость объемов работ", A4),
    ])
    c = describe_volume(path, "rd.pdf")

    kinds = dict(c.tables_by_kind)
    assert kinds.get("equipment_specification") == 1, c.tables_by_kind
    assert kinds.get("work_volumes") == 1, c.tables_by_kind


def test_report_says_what_can_and_cannot_be_summarised(tmp_path):
    """Главное требование к отчёту: он объясняет, ПОЧЕМУ сводки требований
    может не быть, а не оставляет пустое место."""
    path = _pdf(tmp_path / "rd.pdf", [("", A1_LANDSCAPE)] * 5)
    text = render_composition([describe_volume(path, "rd.pdf")])

    assert "листов" in text and "чертеж" in text.lower()
    assert "требован" in text.lower(), "объяснено, что с требованиями из текста"


def test_broken_file_is_named_not_silently_skipped(tmp_path):
    """Битый файл — видимое состояние с причиной (Г.10)."""
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    c = describe_volume(str(bad), "broken.pdf")
    assert c.error is not None
    assert "broken.pdf" in render_composition([c])


def test_specification_sheet_is_recognised_despite_long_header(tmp_path):
    """Г.95 — общий порог в 800 символов отбрасывал лист спецификации: у него
    заголовок таблицы и названия столбцов дают 1100-1500 символов (замер на
    трёх реальных томах). Раньше состав показывал «таблиц нет» на томе, где
    спецификация есть."""
    header = ("Спецификация оборудования, изделий и материалов\n"
              + "Наименование Тип марка Код Завод Единица Количество\n" * 20)
    path = _pdf(tmp_path / "spec.pdf", [(header, A4)])
    c = describe_volume(path, "spec.pdf")
    assert dict(c.tables_by_kind).get("equipment_specification") == 1, c.tables_by_kind


def test_prose_page_mentioning_specification_is_not_counted_as_one(tmp_path):
    """Обратная сторона поднятого порога: страница прозы, где спецификация
    лишь упомянута, таблицей считаться не должна."""
    # Длинные строки, а не много коротких: текст ниже нижнего края листа
    # pymupdf не сохраняет, и фикстура молча получалась бы короткой.
    prose = ("Спецификация оборудования приведена в отдельном документе. "
             + "Далее идёт обычный связный текст пояснительной записки, который "
               "занимает страницу целиком и делает её прозой, а не таблицей. " * 30)
    path = _pdf(tmp_path / "prose.pdf", [(prose, A4)])
    c = describe_volume(path, "prose.pdf")
    assert dict(c.tables_by_kind).get("equipment_specification") is None, c.tables_by_kind


def test_graphic_sheets_are_listed_with_their_names(tmp_path):
    """Г.98 — сводка обязана покрывать ВЕСЬ документ. Прямое указание
    пользователя: «весь документ сканирует и должна быть сводка... значит
    граф и другие материалы игнорятся, их можно в сводке отобразить, что на
    таких-то листах граф материал». Наименование чертежа берётся из штампа
    текстом: он остаётся текстом чаще, чем содержимое листа (Г.59)."""
    path = _pdf(tmp_path / "gr.pdf", [("", A1_LANDSCAPE)],
                stamp="План 1 этажа (вентиляция)\nЛист 17")
    c = describe_volume(path, "gr.pdf")

    assert c.sheets, "перечень листов построен"
    named = [s for s in c.sheets if s.name]
    assert named and "План 1 этажа" in named[0].name, c.sheets


def test_sheets_without_readable_stamp_are_named_as_such(tmp_path):
    """Лист, у которого и штамп в кривых, не пропадает из сводки: он
    попадает туда как «наименование не прочитано» — пропуск обязан быть
    видимым состоянием (Г.10)."""
    path = _pdf(tmp_path / "gr.pdf", [("", A1_LANDSCAPE), ("", A1_LANDSCAPE)])
    c = describe_volume(path, "gr.pdf")
    text = render_composition([c])

    assert len(c.sheets) == 2
    assert all(s.name is None for s in c.sheets)
    assert "наименование не прочитано" in text


def test_identical_sheet_names_collapse_into_one_line_with_pages(tmp_path):
    """Как и в сводке требований (Г.90): повтор сворачивается в одну строку
    со списком листов, иначе перечень из 700 листов нечитаем."""
    path = _pdf(tmp_path / "gr.pdf", [("", A1_LANDSCAPE)] * 3,
                stamp="Принципиальная схема системы\nЛист 5")
    text = render_composition([describe_volume(path, "gr.pdf")])

    assert text.count("Принципиальная схема системы") == 1, text
    assert "листы 1, 2, 3" in text or "листы 1-3" in text, text


def test_plan_content_caught_in_the_stamp_zone_is_not_taken_for_a_sheet_name():
    """Г.98 — замер на реальных томах: зона штампа на листе А0/А1 занимает
    четверть площади, и в неё попадает содержимое чертежа. В перечень шли
    обрывки «икация помещений 1 этажа Наименование» и «188 Холодный 186
    Овощной» — причём второе на листе, где шифр и номер прочитаны верно.
    Выдуманное название хуже честного «не прочитано» (Г.11/Г.10)."""
    from app.document_composition import _clean_sheet_name

    assert _clean_sheet_name("икация помещений 1 этажа Наименование") is None
    assert _clean_sheet_name("188 Холодный 186 Овощной") is None
    assert _clean_sheet_name("") is None and _clean_sheet_name(None) is None

    assert _clean_sheet_name("План 1 этажа (вентиляция)") == "План 1 этажа (вентиляция)"
    assert _clean_sheet_name("Принципиальная схема системы отопления") is not None
    assert _clean_sheet_name("Ведомость объемов работ") is not None
    assert _clean_sheet_name("Спецификация оборудования") is not None
