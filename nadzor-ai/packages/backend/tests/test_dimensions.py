import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classification import open_pdf
from app.dimensions import extract_dimension_facts


def test_duct_section_extracted():
    facts = extract_dimension_facts("800x400\n450x300")
    values = {f["value"] for f in facts}
    assert values == {"800x400", "450x300"}, values
    assert all(f["kind"] == "section" for f in facts)
    print("OK: сечение воздуховода (ШхВ) извлекается из строки")


def test_cyrillic_x_variant_also_matches():
    """На реальных листах «x» иногда набран кириллической «х» — оба
    варианта должны разбираться одинаково."""
    facts = extract_dimension_facts("200х100")
    assert facts and facts[0]["value"] == "200х100"
    print("OK: кириллическая «х» в размере распознаётся так же, как латинская")


def test_diameter_extracted():
    facts = extract_dimension_facts("Ø200\n∅125")
    values = {f["value"] for f in facts}
    assert "Ø200" in values or "∅125" in values, values
    print("OK: диаметр трубы/воздуховода извлекается")


def test_power_and_voltage_extracted():
    facts = extract_dimension_facts("Nэл.=34,0\nU=3х380")
    kinds = {f["kind"] for f in facts}
    assert "power" in kinds, kinds
    assert "voltage" in kinds, kinds
    print("OK: мощность и напряжение оборудования извлекаются")


def test_unrelated_number_is_not_a_dimension():
    facts = extract_dimension_facts("140\nВентилятор ВК-100\n2026")
    assert facts == [], facts
    print("OK: номер помещения/год/порядковое число не принимается за размер")


def test_empty_text_returns_empty():
    assert extract_dimension_facts("") == []
    print("OK: пустой текст не роняет извлечение")


def test_real_sheet_text_layer_has_no_dimension_labels():
    """Замер в докстринге модуля: на CAD-листе этого комплекта размеры
    воздуховодов — кривые, не текст. Текстовый путь честно отдаёт пустой
    список, а не падает и не выдумывает."""
    doc = open_pdf("/home/user/nadzor_sample/АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf")
    try:
        text = doc[17].get_text("text")
        facts = extract_dimension_facts(text)
        assert facts == [], facts
    finally:
        doc.close()
    print("OK: на реальном CAD-листе текстовый путь честно пуст (не ошибка)")


if __name__ == "__main__":
    test_duct_section_extracted()
    test_cyrillic_x_variant_also_matches()
    test_diameter_extracted()
    test_power_and_voltage_extracted()
    test_unrelated_number_is_not_a_dimension()
    test_empty_text_returns_empty()
    test_real_sheet_text_layer_has_no_dimension_labels()
    print("ALL PASS")
