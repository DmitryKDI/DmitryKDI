"""Тесты для equip_cross_check.py — кросс-проверка оборудования ПД↔РД."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.equip_cross_check import cross_check_equipment, render_equip_cross_check_report
from app.matching import DocumentInput


def ef(page, key, name, qty=None, parent=None):
    fact = {"page": page, "key": key, "name": name}
    if qty:
        fact["qty"] = qty
    if parent:
        fact["parent"] = parent
    return fact


def test_missing_in_rd():
    """Позиция есть в ПД, нет в РД — finding_type='missing_in_rd'."""
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[
        ef(1, "14", "Приточная установка", "2"), ef(1, "15", "Вентилятор", "1"),
    ])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ",
                           equipment_facts=[ef(1, "15", "Вентилятор", "1")])]
    result = cross_check_equipment(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "missing_in_rd"
    assert result.findings[0].equip_key == "14"
    assert result.missing_in_rd == ["14"]
    print("OK: позиция из ПД, отсутствующая в РД — находка missing_in_rd")


def test_missing_in_pd():
    """Позиция есть в РД, но не было в ПД — finding_type='missing_in_pd',
    менее критично (добавление, а не пропажа)."""
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ",
                            equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ", equipment_facts=[
        ef(1, "14", "Приточная установка", "2"), ef(1, "16", "Клапан обратный", "1"),
    ])]
    result = cross_check_equipment(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "missing_in_pd"
    assert result.findings[0].severity == "незначительно"
    assert result.missing_in_pd == ["16"]
    print("OK: позиция, добавленная в РД без соответствия в ПД — находка missing_in_pd")


def test_qty_changed():
    """Позиция есть в обеих, количество отличается — реальный случай
    нарушения №1 (пропавшая парная установка): один агрегат вместо двух."""
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ",
                            equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ",
                           equipment_facts=[ef(1, "14", "Приточная установка", "1")])]
    result = cross_check_equipment(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "qty_changed"
    assert "2" in result.findings[0].detail and "1" in result.findings[0].detail
    print("OK: изменённое количество позиции, присутствующей в обеих сторонах — находка qty_changed")


def test_matching_qty_no_finding():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ",
                            equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ",
                           equipment_facts=[ef(1, "14", "Приточная установка (уточнено)", "2")])]
    result = cross_check_equipment(before, after)
    # Название намеренно не сравнивается (Г.28 — слишком шумно) — только qty
    assert len(result.findings) == 0
    assert result.matched_equip == 1
    print("OK: совпавшее количество не даёт находки, даже если название текстуально отличается")


def test_render_report():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[
        ef(1, "14", "Приточная установка", "2"), ef(1, "15", "Вентилятор", "1"),
    ])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ", equipment_facts=[
        ef(1, "14", "Приточная установка", "1"),
    ])]
    result = cross_check_equipment(before, after)
    report = render_equip_cross_check_report(result)
    assert "Кросс-проверка оборудования" in report
    assert "Отсутствует" in report or "отсутствует" in report.lower()
    assert result.total_pd_equip == 2
    assert result.total_rd_equip == 1
    assert result.matched_equip == 1
    assert len(result.findings) == 2  # 15 отсутствует + 14 qty_changed
    print("OK: отчёт по оборудованию содержит все секции и совпадает со счётчиками результата")


if __name__ == "__main__":
    test_missing_in_rd()
    test_missing_in_pd()
    test_qty_changed()
    test_matching_qty_no_finding()
    test_render_report()
    print("ALL PASS")
