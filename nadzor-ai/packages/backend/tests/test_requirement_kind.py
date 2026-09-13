"""Разделение извлечённого на требования, исходные данные и ведомость (Г.117).

Наблюдение с реального комплекта: из 531 строки разбора 508 ушли в «требует
проверки», и большинство было нечего проверять по природе — исходные данные
расчёта и строки ведомости оборудования. Здесь проверяется, что они
различаются ПО ФОРМЕ ЗАПИСИ, без знания темы документа.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.requirement_kind import (KIND_INPUT_DATA, KIND_REQUIREMENT,  # noqa: E402
                                  KIND_SPEC_ITEM, classify, split_by_kind)


def test_action_and_obligation_are_requirements():
    for sentence in (
        "Трубопроводы отопления в местах нахождения учащихся прокладывать в шахтах",
        "Все приборы отопления должны быть травмобезопасного исполнения",
        "Высота установки отопительных приборов на путях эвакуации — не менее 2,2 м",
        "Предусмотрена система приточной вентиляции с переменной рециркуляцией",
        "Внутренние блоки оснащены дренажными помпами",
        "Воздуховоды из оцинкованной стали по ГОСТ 14918-80, класс герметичности «В»",
    ):
        assert classify(sentence) == KIND_REQUIREMENT, sentence
    print("OK: действие, обязанность и нормативная граница — требования")


def test_bare_measured_values_are_input_data():
    """Величина без границы и без действия — основание расчёта, а не
    требование к объекту: подтверждать её в рабочей документации нечем."""
    for sentence in (
        "Теплота сгорания дерева — 13,8 МДж/кг",
        "Коэффициент ksm — 1,2",
        "Площадь коридора — 217 м²",
        "Высота двери — 2,1 м",
        "Температура наружного воздуха — 26 °C",
        "Масса горючих веществ в помещении — 250 кг",
    ):
        assert classify(sentence) == KIND_INPUT_DATA, sentence
    print("OK: измеренная величина без границы — исходные данные расчёта")


def test_equipment_designations_are_specification_rows():
    for sentence in (
        "Вытяжная установка WNK 160/1",
        "Приточная установка WNP 70-40/31.2D, 1 компл.",
        "Установка вытяжная дымоудаления KDV DU 400-80A-3х10",
        "Блок внутренний настенный VRF системы KF-IW-22B-V",
    ):
        assert classify(sentence) == KIND_SPEC_ITEM, sentence
    print("OK: марка изделия без действия — позиция ведомости")


def test_a_norm_reference_alone_does_not_make_a_specification_row():
    """«по ГОСТ 3262-75» — не марка изделия. Иначе ведомостью стало бы любое
    требование, где назван норматив."""
    assert classify("Трубопровод из водогазопроводных труб по ГОСТ 3262-75") == KIND_REQUIREMENT
    assert classify("Регистры из бесшовных труб по ГОСТ 8732-78") == KIND_REQUIREMENT
    print("OK: ссылка на норматив не превращает требование в позицию ведомости")


def test_unknown_shape_defaults_to_requirement():
    """Тихо не проверить хуже, чем проверить лишнее (Г.10)."""
    assert classify("Независимое присоединение систем отопления и вентиляции") == KIND_REQUIREMENT
    assert classify("") == KIND_REQUIREMENT
    print("OK: неопознанная форма идёт в требования, а не выбрасывается")


def test_split_keeps_every_line():
    """Ни одна строка не исчезает: сумма по видам равна исходному числу."""
    class Fake:
        def __init__(self, sentence): self.sentence = sentence

    items = [Fake("Вытяжная установка WNK 160/1"),
             Fake("Площадь коридора — 217 м²"),
             Fake("Приборы отопления должны быть травмобезопасными"),
             Fake("Установка WNP 60-30/28.4D")]
    groups = split_by_kind(items)
    assert sum(len(v) for v in groups.values()) == len(items)
    assert len(groups[KIND_SPEC_ITEM]) == 2, groups
    assert len(groups[KIND_INPUT_DATA]) == 1, groups
    assert len(groups[KIND_REQUIREMENT]) == 1, groups
    print("OK: разделение ничего не теряет")
