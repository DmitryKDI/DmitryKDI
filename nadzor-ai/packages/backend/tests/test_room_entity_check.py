"""Тесты общей сверки «назначено в ПД ↔ нарисовано в РД» (Г.88).

Главное, что здесь проверяется, — что механизм НЕ знает про конкретный
раздел: те же три типа находок должны получаться на разделе, для которого
никаких специальных строк в коде не писали.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re  # noqa: E402

from app import room_entity_check  # noqa: E402
from app.room_entity_check import (  # noqa: E402
    _plan_prompt,
    _table_prompt,
    cross_check_entities,
    extract_table_page,
    find_uncovered_rooms,
)
from app.table_registry import TableKind  # noqa: E402

VENT = TableKind(
    kind="ventilation_balance", discipline_hint="ОВ",
    title_re=re.compile("воздухообмен"), description_ru="Таблица воздухообменов — системы",
    status="n=1", entity_name="ветка местного отсоса", entity_examples="«В2.7»",
    system_column="система вентиляции", entity_column="местные отсосы",
)
WATER = TableKind(
    kind="water_balance", discipline_hint="ВК",
    title_re=re.compile("водопотреблен"), description_ru="Баланс водопотребления — стояки",
    status="n=0", entity_name="стояк водоснабжения", entity_examples="«Ст-1»",
    system_column="система водоснабжения", entity_column="обозначение стояка",
)


def test_findings_are_the_same_for_a_section_nobody_coded_for():
    """Ключевая проверка обобщения: механизм не знает про раздел. Те же три
    типа расхождений должны находиться на ВК — разделе, под который в коде
    не написано ни одной строки, только запись в реестре таблиц."""
    pd_rooms = [
        {"room": "12", "system": "В1", "entities": ["Ст-1"]},   # нарисован не там
        {"room": "13", "system": "В1", "entities": ["Ст-2"]},   # не нарисован вовсе
        {"room": "14", "system": "В1", "entities": ["Ст-3"]},   # система другая
    ]
    rd_entities = [
        {"entity": "Ст-1", "nearest_room": "99", "system": "В1"},
        {"entity": "Ст-3", "nearest_room": "14", "system": "В2"},
    ]
    findings = cross_check_entities(pd_rooms, rd_entities)
    types = {f.room: f.finding_type for f in findings}

    assert types == {"12": "entity_relocated", "13": "entity_missing", "14": "system_mismatch"}
    print("OK: три типа расхождений находятся на разделе, под который кода не писали")


def test_prompts_are_built_from_registry_not_hardcoded():
    """Раньше названия столбцов и вид обозначений были зашиты в код. Теперь
    промпт обязан меняться вместе с записью реестра — иначе обобщение
    формальное: подставили поля, а модель читает всё равно про вентиляцию."""
    vent_table, water_table = _table_prompt(VENT), _table_prompt(WATER)
    vent_plan, water_plan = _plan_prompt(VENT), _plan_prompt(WATER)

    assert "местные отсосы" in vent_table and "обозначение стояка" in water_table
    assert "ветка местного отсоса" in vent_plan and "стояк водоснабжения" in water_plan
    assert "воздухообмен" not in water_table.lower(), "лексика ОВ не должна течь в чужой раздел"
    assert "отсос" not in water_plan.lower()
    print("OK: промпты собираются из реестра и не тащат лексику чужого раздела")


def test_uncovered_rooms_are_reported_not_silently_dropped():
    """Г.60 — «строки в таблице нет вообще» отличимо от «строка есть, но
    столбец пуст»: первое значит, что таблица не тот источник."""
    assert find_uncovered_rooms(["140", "314"], {"140", "141"}) == ["314"]
    print("OK: помещение, которого нет в таблице ни строкой, названо явно")


def test_call_failure_is_distinguishable_from_empty_page():
    """Г.10/Г.73 — сбой вызова не должен выглядеть как «на листе пусто»."""
    def boom(*a, **kw):
        raise ConnectionError("сеть недоступна")

    original = room_entity_check.call_llm_json
    room_entity_check.call_llm_json = boom
    original_render = room_entity_check.render_page_to_data_url
    room_entity_check.render_page_to_data_url = lambda *a, **kw: "data:,"
    try:
        result = extract_table_page("любой.pdf", 1, VENT, config=None)
    finally:
        room_entity_check.call_llm_json = original
        room_entity_check.render_page_to_data_url = original_render

    assert result["rooms"] == [] and result["rooms_seen"] == []
    assert result["error"] is not None and "сеть недоступна" in result["error"]
    print("OK: сбой вызова помечен явно, а не выдан за пустой лист")


def test_entity_drawn_at_the_right_room_is_not_a_finding():
    """Совпадение не должно давать ложную находку — иначе отчёт утонет."""
    findings = cross_check_entities(
        [{"room": "140", "system": "П6", "entities": ["В2.7"]}],
        [{"entity": "В2.7", "nearest_room": "140", "system": "П6"}],
    )
    assert findings == []
    print("OK: совпавшее назначение расхождением не считается")
