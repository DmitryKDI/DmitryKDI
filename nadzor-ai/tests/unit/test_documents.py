"""Разбор документов и извлечение фактов с координатами."""
from __future__ import annotations

from pathlib import Path

from documents.extract import extract_facts, join_wrapped
from documents.pdf import Block, Cell, PageData, TableData, parse
from documents.schemas import DocKind, StateKind
from documents.stamp import detect_kind, parse_stamp

ROOT = Path(__file__).resolve().parents[2]
PD3 = ROOT / "data/demo/generated/OBJ-001/pd_rev3/2024-15-KR_izm3.pdf"
AOSR = ROOT / "data/demo/generated/OBJ-001/as_built/aosr_47.pdf"


def test_document_kind_is_detected_by_content():
    """Вид документа определяется по заголовку, а не по имени файла."""
    assert detect_kind(parse(PD3.read_bytes())) == (DocKind.PD, StateKind.PD)
    assert detect_kind(parse(AOSR.read_bytes())) == (DocKind.AOSR, StateKind.AS_BUILT)


def test_stamp_is_parsed():
    stamp = parse_stamp(parse(PD3.read_bytes()))
    assert stamp.code == "2024-15-КР"
    assert stamp.revision == "3"
    assert stamp.stage == "П"
    assert stamp.doc_date.isoformat() == "2024-06-26"
    assert stamp.confidence >= 0.8


def test_every_fact_has_source_reference():
    """Факт без координат в систему не попадает."""
    facts = extract_facts(parse(AOSR.read_bytes()), "D1", "F1")
    assert facts
    for fact in facts:
        assert fact.source.page >= 1
        x0, y0, x1, y1 = fact.source.bbox
        assert x1 > x0 and y1 > y0


def test_specification_rows_are_extracted():
    facts = extract_facts(parse(PD3.read_bytes()), "D1", "F1")
    elements = {f.key: f.value for f in facts if f.fact_type == "element"}
    assert elements["ПП-8"]["concrete_class"] == "B25"
    assert elements["ПП-8"]["size"] == "180 мм"
    assert elements["К-2"]["rebar"] == "A400"


def test_wrapped_cells_are_joined_correctly():
    assert join_wrapped("+3.000…+75.00\n0") == "+3.000…+75.000"
    assert join_wrapped("Многоквартирный жилой дом\nс автостоянкой") == \
        "Многоквартирный жилой дом с автостоянкой"


def test_room_facts_from_plan_label_and_schedule_row():
    """Номер помещения читается и с подписи на плане ПД, и со строки «Экспликации» ИД.

    Форма записи разная (площадь есть только во второй), но ключ сшивки —
    номер помещения — извлекается одним и тем же образом.
    """
    page = PageData(page=1, width=1000, height=1000, blocks=[
        Block(page=1, text="140 Физического эксперимента", bbox=(10, 10, 100, 20), size=10.4),
        Block(page=1, text="310 помещение для хранения оборудования 10.6 В3",
             bbox=(10, 40, 200, 50), size=9.8),
        Block(page=1, text="4500", bbox=(10, 70, 30, 80), size=8.0),      # размерная цепочка
    ])
    facts = extract_facts([page], "D1", "F1")
    rooms = {f.key: f.value for f in facts if f.fact_type == "room"}

    assert rooms["140"] == {"room_no": "140", "name": "Физического эксперимента",
                            "area": "", "category": ""}
    assert rooms["310"] == {"room_no": "310", "name": "помещение для хранения оборудования",
                            "area": "10.6", "category": "В3"}
    assert "4500" not in rooms


def test_room_facts_from_combined_list_label():
    """Общая подпись сразу на несколько однотипных помещений — частый случай на схемах МГН.

    Найдено на реальном комплекте документов при слепой проверке: подпись
    «267, 270 Раздевальная и санузул для МГН» без этого разбора не даёт ни
    одного факта о помещении — при том, что оба номера реальны и совпадают
    с исполнительной документацией.
    """
    page = PageData(page=1, width=1000, height=1000, blocks=[
        Block(page=1, text="267, 270 Раздевальная и санузул для МГН",
             bbox=(10, 10, 200, 20), size=9.0),
    ])
    facts = extract_facts([page], "D1", "F1")
    rooms = {f.key: f.value for f in facts if f.fact_type == "room"}
    assert rooms["267"]["name"] == "Раздевальная и санузул для МГН"
    assert rooms["270"]["name"] == "Раздевальная и санузул для МГН"
    assert rooms["267"]["room_no"] == "267" and rooms["270"]["room_no"] == "270"


def test_narrative_text_survives_a_false_positive_whole_page_table():
    """Найдено на реальном комплекте: на насыщенном листе общих данных поиск таблиц
    иногда принимает за таблицу весь лист целиком, а её заголовок системе неизвестен —
    раньше это тихо вырезало весь пояснительный текст листа вместе с таблицей.
    Пояснительный абзац должен уцелеть, если реальных фактов таблица не дала.
    """
    narrative = ("В помещениях раздевальных, санузлов и душевых для МГН (пом. 267, 270) "
                "предусмотрена система подогрева полов, совмещённая с отоплением.")
    bogus_table = TableData(
        page=1, bbox=(0, 0, 1000, 1000), header=["непонятный", "заголовок"],
        rows=[[Cell(text="мусор", bbox=(0, 0, 10, 10))]])
    page = PageData(page=1, width=1000, height=1000,
                    blocks=[Block(page=1, text=narrative, bbox=(50, 50, 900, 70), size=10.0)],
                    tables=[bogus_table])
    facts = extract_facts([page], "D1", "F1")
    texts = [f.value for f in facts if f.fact_type == "text"]
    assert any("подогрева полов" in t for t in texts)
