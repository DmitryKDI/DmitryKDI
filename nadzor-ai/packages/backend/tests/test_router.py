"""Тесты для router.py — детерминированного слоя маршрутизации.

Проверяют три уровня:
  0 — реестры совпали → SKIP
  2 — расхождения в реестрах → LLM обязателен
  3 — текстовый diff > порога → LLM условно
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput, PagePair
from app.router import (
    PairVerdict,
    TEXT_DIFF_THRESHOLD,
    _equip_match,
    _rooms_match,
    _text_diff_count,
    classify_all_pairs,
    classify_pair,
    classify_text_pair,
    render_report,
)


def tf(page, text):
    return {"page": page, "text": text}


def rf(page, key, name, area=None):
    fact = {"page": page, "key": key, "name": name}
    if area:
        fact["area"] = area
    return fact


def ef(page, key, name, qty=None):
    fact = {"page": page, "key": key, "name": name}
    if qty:
        fact["qty"] = qty
    return fact


def dummy_pair(before_file=0, before_page=1, after_file=0, after_page=1,
               score=0.5, matched_by="text", kind="drawing"):
    return PagePair(before_file, before_page, after_file, after_page,
                    score, matched_by, kind)


# ---------- _rooms_match ----------


def test_rooms_match_identical():
    b = [rf(1, "012", "Венткамера", "15.2"), rf(1, "013", "Форкамера", "12.0")]
    a = [rf(1, "012", "Венткамера", "15.2"), rf(1, "013", "Форкамера", "12.0")]
    ok, diffs = _rooms_match(b, a)
    assert ok is True
    assert diffs == []
    print("OK: идентичные реестры помещений — совпадение")


def test_rooms_match_one_missing_in_after():
    b = [rf(1, "012", "Венткамера", "15.2"), rf(1, "013", "Форкамера", "12.0")]
    a = [rf(1, "012", "Венткамера", "15.2")]
    ok, diffs = _rooms_match(b, a)
    assert ok is False
    assert any("отсутствует в РД" in d for d in diffs)
    assert len(diffs) == 1
    print("OK: пропавшее в РД помещение — расхождение")


def test_rooms_match_name_changed():
    b = [rf(1, "012", "Венткамера", "15.2")]
    a = [rf(1, "012", "Тепловая", "15.2")]
    ok, diffs = _rooms_match(b, a)
    assert ok is False
    assert "ПД «Венткамера» → РД «Тепловая»" in diffs[0]
    print("OK: изменённое название — расхождение")


def test_rooms_match_area_changed():
    b = [rf(1, "012", "Венткамера", "15.2")]
    a = [rf(1, "012", "Венткамера", "18.0")]
    ok, diffs = _rooms_match(b, a)
    assert ok is False
    assert "площадь" in diffs[0]
    print("OK: изменённая площадь — расхождение")


# ---------- _equip_match ----------


def test_equip_match_identical():
    b = [ef(1, "14", "Приточная установка", "1"), ef(1, "14.1", "Вентилятор", "1")]
    a = [ef(1, "14", "Приточная установка", "1"), ef(1, "14.1", "Вентилятор", "1")]
    ok, diffs = _equip_match(b, a)
    assert ok is True
    assert diffs == []
    print("OK: идентичные реестры оборудования — совпадение")


def test_equip_match_qty_changed():
    b = [ef(1, "14", "Приточная установка", "2")]
    a = [ef(1, "14", "Приточная установка", "1")]
    ok, diffs = _equip_match(b, a)
    assert ok is False
    assert "кол-во" in diffs[0]
    print("OK: изменённое количество оборудования — расхождение")


# ---------- _text_diff_count ----------


def test_text_diff_low_count():
    """Перенумерация позиций без смыслового отличия — мало diff-операций."""
    before = "10 Воздухонагреватель\n11 Калорифер\n12 Вентилятор"
    after = "1 Воздухонагреватель\n2 Калорифер\n3 Вентилятор"
    count = _text_diff_count(before, after)
    assert count < TEXT_DIFF_THRESHOLD
    print("OK: перенумерация без смыслового отличия — мало diff-операций")


def test_text_diff_high_count():
    before_lines = [f"Позиция {i}. Бетон B30. Цемент М500. Объём {i*0.5} м3." for i in range(1, 25)]
    after_lines = [f"Позиция {i}. Бетон B25. Цемент М400. Объём {i*0.3} м3." for i in range(1, 25)]
    count = _text_diff_count("\n".join(before_lines), "\n".join(after_lines))
    assert count > TEXT_DIFF_THRESHOLD
    print("OK: реально изменённый текст — много diff-операций")


# ---------- classify_pair (drawing pairs with rooms) ----------


def test_classify_pair_rooms_identical_skip():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    v = classify_pair(dummy_pair(), before, after, [], [])
    assert v.level == 0
    assert "совпали" in v.reason
    print("OK: совпавшие реестры помещений — уровень 0")


def test_classify_pair_rooms_differ_llm():
    before = [DocumentInput("pd.pdf", 1, [],
                            [rf(1, "012", "Венткамера", "15.2"), rf(1, "013", "Форкамера", "12.0")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    v = classify_pair(dummy_pair(), before, after, [], [])
    assert v.level == 2
    assert "комнаты" in v.reason
    print("OK: разошедшиеся реестры помещений — уровень 2")


def test_classify_pair_equip_differ_llm():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ",
                            equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ",
                           equipment_facts=[ef(1, "14", "Приточная установка", "1")])]
    v = classify_pair(dummy_pair(), before, after, [], [])
    assert v.level == 2
    assert "оборудование" in v.reason
    print("OK: разошедшийся реестр оборудования — уровень 2")


def test_classify_pair_no_facts_skip():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ")]
    v = classify_pair(dummy_pair(), before, after, [], [])
    assert v.level == 0
    print("OK: нет данных реестров — уровень 0 (нечего сравнивать)")


def test_classify_pair_price_page_skip_even_without_room_diff():
    """Реальный случай прогона: страница спецификации поставщика (артикулы
    без кириллицы, без номеров помещений) должна уходить в уровень 0, а не
    в 2 — даже если формально позиции 'не совпали' построчно."""
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[
        ef(1, "1", "PP-R", "1"), ef(1, "2", "В6.3", "1"), ef(1, "3", "компл.", "1"),
    ])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ", equipment_facts=[])]
    v = classify_pair(dummy_pair(), before, after, [], [])
    assert v.level == 0
    assert "прайс" in v.reason
    print("OK: страница прайса поставщика без номеров помещений — уровень 0, не 2")


# ---------- classify_text_pair ----------


def test_text_pair_low_diff_skip():
    v = classify_text_pair(dummy_pair(kind="text"), "10 Воздухонагреватель", "1 Воздухонагреватель")
    assert v.level == 0
    print("OK: маленький текстовый diff — уровень 0")


def test_text_pair_high_diff_conditional():
    before = "\n".join(f"Позиция {i}. Описание элемента {i}." for i in range(1, 30))
    after = "\n".join(f"Позиция {i}. Новое описание элемента {i}." for i in range(1, 30))
    v = classify_text_pair(dummy_pair(kind="text"), before, after)
    assert v.level == 3
    assert v.diff_count > TEXT_DIFF_THRESHOLD
    print("OK: большой текстовый diff — уровень 3")


# ---------- classify_all_pairs ----------


def test_classify_all_pairs_groups_levels():
    before = [
        DocumentInput("pd.pdf", 2, [],
                       [rf(1, "012", "Венткамера", "15.2"), rf(2, "013", "Форкамера", "12.0")], "ОВ"),
    ]
    after = [
        DocumentInput("rd.pdf", 2, [],
                       [rf(1, "012", "Венткамера", "15.2"), rf(2, "013", "Форкамера", "12.0")], "ОВ"),
        DocumentInput("rd2.pdf", 2, [],
                       [rf(1, "012", "Венткамера", "15.2"), rf(2, "013", "Тепловая", "12.0")], "ОВ"),
    ]
    pairs = [
        dummy_pair(before_file=0, before_page=1, after_file=0, after_page=1),
        dummy_pair(before_file=0, before_page=2, after_file=1, after_page=2),
    ]
    verdicts = classify_all_pairs(pairs, before, after, [], [])
    assert len(verdicts) == 2
    assert verdicts[0].level == 0
    assert verdicts[1].level == 2
    print("OK: пачка пар корректно группируется по уровням")


# ---------- render_report ----------


def test_render_report_format():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    verdicts = classify_all_pairs([dummy_pair()], before, after, [], [])
    report = render_report(verdicts)
    assert "ИТОГО пар:" in report
    assert "Уровень 0" in report and "Уровень 2" in report and "Уровень 3" in report
    assert "совпали" in report
    assert "score=" in report
    print("OK: отчёт содержит все три уровня и читаем")


if __name__ == "__main__":
    test_rooms_match_identical()
    test_rooms_match_one_missing_in_after()
    test_rooms_match_name_changed()
    test_rooms_match_area_changed()
    test_equip_match_identical()
    test_equip_match_qty_changed()
    test_text_diff_low_count()
    test_text_diff_high_count()
    test_classify_pair_rooms_identical_skip()
    test_classify_pair_rooms_differ_llm()
    test_classify_pair_equip_differ_llm()
    test_classify_pair_no_facts_skip()
    test_classify_pair_price_page_skip_even_without_room_diff()
    test_text_pair_low_diff_skip()
    test_text_pair_high_diff_conditional()
    test_classify_all_pairs_groups_levels()
    test_render_report_format()
    print("ALL PASS")
