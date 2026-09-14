"""Сверка ведомости оборудования ПД с рабочей документацией (Г.117).

Прежний способ проверял каждую позицию как отдельное требование: десятки
одинаковых строк «требует проверки» и ложные подтверждения по имени завода.
Здесь это сравнение двух списков по обозначению изделия.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.spec_compare import (STATUS_MISSING, STATUS_PRESENT, STATUS_SIMILAR,  # noqa: E402
                              SpecEntry, collect, compare, designations, normalize, render)


class Req:
    def __init__(self, sentence, page=1, document="Том 5.2.1", section="ОВ"):
        self.sentence, self.page, self.document, self.section = sentence, page, document, section


def test_designation_is_read_as_a_chain_of_tokens():
    """Марка и типоразмер в документах разнесены пробелом так же часто, как
    написаны слитно — обозначение читается цепочкой."""
    assert designations("Вытяжная установка WNK 160/1") == ["WNK 160/1"]
    assert designations("Установка KDV DU 400-80A-3х10") == ["KDV DU 400-80A-3X10"]
    assert designations("Блок KF-IW-22B-V") == ["KF-IW-22B-V"]
    print("OK: обозначение читается целиком, включая типоразмер через пробел")


def test_norm_reference_is_not_a_designation():
    assert designations("Трубопровод по ГОСТ 3262-75") == []
    assert designations("Воздуховоды по ГОСТ 14918-80 класс «В»") == []
    print("OK: ссылка на норматив обозначением изделия не считается")


def test_cyrillic_lookalikes_do_not_split_one_product_in_two():
    """«3х10» и «3x10» — одно изделие: в документах буквы вперемешку."""
    assert normalize("KDV DU 400-80A-3х10") == normalize("KDV DU 400-80A-3x10")
    print("OK: кириллические двойники приведены к латинице")


def test_same_designation_from_many_pages_is_one_row():
    entries = collect([Req("Установка вытяжная WNK 160/1", page=108),
                       Req("Установка вытяжная WNK 160/1", page=109),
                       Req("Установка вытяжная WNK 250/1", page=111)])
    assert len(entries) == 2, entries
    wnk160 = next(e for e in entries if e.designation == "WNK 160/1")
    assert sorted(wnk160.pages) == [108, 109]
    print("OK: одна марка с разных страниц — одна строка со всеми страницами")


def test_brand_name_alone_does_not_confirm_a_product():
    """Главная ошибка прежнего способа: слово «KORF» где-то в РД засчитывалось
    как подтверждение установки KDV DU 400-80A-3х10."""
    entries = [SpecEntry(designation="KDV DU 400-80A-3X10")]
    result = compare(entries, "Оборудование поставки завода KORF по проекту")
    assert result.diffs[0].status == STATUS_MISSING, result.diffs[0]
    print("OK: имя завода не подтверждает конкретное изделие")


def test_present_and_replaced_are_told_apart():
    entries = [SpecEntry(designation="WNK 160/1"), SpecEntry(designation="WNK 250/1")]
    result = compare(entries, "Ведомость РД: установка WNK 160/1; установка WNK 315/1")
    statuses = {d.entry.designation: d.status for d in result.diffs}
    assert statuses["WNK 160/1"] == STATUS_PRESENT, statuses
    assert statuses["WNK 250/1"] == STATUS_SIMILAR, statuses
    assert "WNK 315/1" in next(d.detail for d in result.diffs
                               if d.entry.designation == "WNK 250/1")
    print("OK: совпавшее, заменённое и отсутствующее различаются")


def test_missing_is_not_stated_as_a_violation():
    """Подписи чертежей часто в кривых: «не найдено» — не «нет изделия» (Г.10)."""
    result = compare([SpecEntry(designation="WNP 60-30/28.4D")], "")
    assert result.diffs[0].status == STATUS_MISSING
    assert "не вывод об отсутствии" in result.diffs[0].detail
    text = render(result)
    assert "не является выводом" in text
    print("OK: отсутствие обозначения не выдаётся за отсутствие изделия")


def test_counts_cover_every_entry():
    entries = [SpecEntry(designation=f"WNK {n}/1") for n in (100, 160, 250)]
    result = compare(entries, "WNK 160/1")
    assert sum(result.counts.values()) == len(entries), result.counts
    print("OK: в счётчиках учтена каждая позиция")
