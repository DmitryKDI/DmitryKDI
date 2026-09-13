"""Тесты для room_cross_check.py — кросс-проверка помещений ПД↔РД."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput
from app.room_cross_check import (
    _is_minor_variation,
    cross_check_rooms,
    render_cross_check_report,
)


def rf(page, key, name, area=None):
    fact = {"page": page, "key": key, "name": name}
    if area:
        fact["area"] = area
    return fact


def test_missing_in_rd():
    """Номер есть в ПД, нет в РД — finding_type='missing_in_rd'."""
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "013", "Форкамера", "12.0")], "ОВ")]
    result = cross_check_rooms(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "missing_in_rd"
    assert result.findings[0].room_key == "012"
    assert "Отсутствует в РД" in result.findings[0].detail
    print("OK: помещение из ПД, отсутствующее в РД — находка missing_in_rd")


def test_name_changed():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Тепловая", "15.2")], "ОВ")]
    result = cross_check_rooms(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "name_changed"
    assert result.findings[0].room_name_rd == "Тепловая"
    print("OK: изменённое название реального (не суффиксного) помещения — находка")


def test_area_changed():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Венткамера", "18.0")], "ОВ")]
    result = cross_check_rooms(before, after)
    assert len(result.findings) == 1
    assert result.findings[0].finding_type == "area_changed"
    assert "15.2" in result.findings[0].detail and "18.0" in result.findings[0].detail
    print("OK: изменённая площадь — находка area_changed")


def test_minor_variation_not_flagged():
    """Уточнение названия (суффикс) — НЕ считается изменением."""
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Венткамера П10", "15.2")], "ОВ")]
    result = cross_check_rooms(before, after)
    assert len(result.findings) == 0
    print("OK: уточнение названия суффиксом не порождает ложную находку")


def test_multiple_rooms():
    before = [DocumentInput("pd.pdf", 1, [], [
        rf(1, "012", "Венткамера", "15.2"),
        rf(1, "140", "Физического", "10.0"),
        rf(1, "142", "Астрономии", "12.0"),
    ], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [
        rf(1, "012", "Венткамера", "15.2"),
    ], "ОВ")]
    result = cross_check_rooms(before, after)
    assert result.total_pd_rooms == 3
    assert result.matched_rooms == 1
    assert result.unmatched_rooms == 2
    assert set(result.missing_anchors) == {"140", "142"}
    print("OK: несколько помещений проверяются независимо")


def test_render_report():
    before = [DocumentInput("pd.pdf", 1, [], [
        rf(1, "012", "Венткамера", "15.2"),
        rf(1, "140", "Физического", "10.0"),
    ], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [
        rf(1, "012", "Тепловая", "18.0"),  # name + area changed
    ], "ОВ")]
    result = cross_check_rooms(before, after)
    report = render_cross_check_report(result)
    assert "Кросс-проверка" in report
    assert "Отсутствует" in report
    assert "изменена" in report or "изменено" in report
    assert result.total_pd_rooms == 2
    assert result.matched_rooms == 1
    assert result.unmatched_rooms == 3  # name_changed + area_changed + 140 missing
    assert len(result.findings) == 3
    print("OK: отчёт содержит все секции и совпадает со счётчиками результата")


def test_is_minor_variation():
    assert _is_minor_variation("Венткамера", "Венткамера П10") is True
    assert _is_minor_variation("Венткамера П10", "Венткамера") is True
    assert _is_minor_variation("Венткамера", "Тепловая") is False
    assert _is_minor_variation("Венткамера", "Венткамера") is True
    assert _is_minor_variation("Коридор", "Коридорный тип") is True
    print("OK: _is_minor_variation различает уточнение и реальное изменение")


if __name__ == "__main__":
    test_missing_in_rd()
    test_name_changed()
    test_area_changed()
    test_minor_variation_not_flagged()
    test_multiple_rooms()
    test_render_report()
    test_is_minor_variation()
    print("ALL PASS")
