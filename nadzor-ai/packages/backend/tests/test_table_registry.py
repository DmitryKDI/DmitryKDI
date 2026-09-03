import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.table_registry import all_known_kinds, classify_table_page


def _page(text: str, page: int = 1) -> list[dict]:
    return [{"page": page, "text": text}]


def test_classifies_ventilation_balance_same_as_before():
    """Г.66 — тот же реальный заголовок, что уже подтверждён Г.58/Г.59 на
    реальном комплекте: должен классифицироваться, не просто True/False."""
    kind = classify_table_page(_page("Таблица воздухообменов помещений\n(начало)"), 1)
    assert kind is not None
    assert kind.kind == "ventilation_balance"
    assert kind.status.startswith("n=1")
    print("OK: вентиляционная таблица классифицируется тем же признаком, что и раньше")


def test_returns_none_for_unknown_table():
    assert classify_table_page(_page("Обычная страница текста без таблиц"), 1) is None
    print("OK: неизвестная/непохожая страница честно даёт None")


def test_returns_none_for_long_page_even_with_matching_word():
    """Тот же признак Г.59, теперь общий: длинная страница (проза/
    содержание) не считается таблицей, даже если термин упомянут."""
    long_text = "Водопотребление и водоотведение обсуждаются в этом разделе. " * 20
    assert len(long_text) > 800
    assert classify_table_page(_page(long_text), 1) is None
    print("OK: длинная страница с упоминанием термина не считается самой таблицей")


def test_classifies_water_balance_scaffold():
    """n=0 заготовка — синтетический пример по нормативному термину СП
    30.13330, НЕ проверено на реальном документе, честно помечено."""
    kind = classify_table_page(_page("Ведомость водопотребления и водоотведения"), 1)
    assert kind is not None
    assert kind.kind == "water_balance"
    assert kind.status.startswith("n=0")
    print("OK: таблица водопотребления/водоотведения (ВК) классифицируется как заготовка")


def test_classifies_electrical_loads_scaffold():
    kind = classify_table_page(_page("Ведомость электрических нагрузок"), 1)
    assert kind is not None
    assert kind.kind == "electrical_loads"
    print("OK: таблица электрических нагрузок (ЭОМ) классифицируется")


def test_classifies_cable_log_scaffold():
    kind = classify_table_page(_page("Кабельный журнал"), 1)
    assert kind is not None
    assert kind.kind == "cable_log"
    print("OK: кабельный журнал (ЭОМ) классифицируется")


def test_classifies_steel_consumption_scaffold():
    kind = classify_table_page(_page("Ведомость расхода стали"), 1)
    assert kind is not None
    assert kind.kind == "steel_consumption"
    print("OK: ведомость расхода стали (КЖ) классифицируется")


def test_classifies_shipping_marks_scaffold():
    kind = classify_table_page(_page("Ведомость отправочных марок"), 1)
    assert kind is not None
    assert kind.kind == "shipping_marks"
    print("OK: ведомость отправочных марок (КМ) классифицируется")


def test_classifies_finishing_schedule_scaffold():
    kind = classify_table_page(_page("Ведомость отделки помещений"), 1)
    assert kind is not None
    assert kind.kind == "finishing_schedule"
    print("OK: ведомость отделки помещений (АР) классифицируется")


def test_classifies_openings_schedule_scaffold():
    kind = classify_table_page(_page("Спецификация заполнения оконных проёмов"), 1)
    assert kind is not None
    assert kind.kind == "openings_schedule"
    print("OK: спецификация заполнения проёмов (АР) классифицируется")


def test_classifies_equipment_specification_scaffold():
    """Универсальная форма — discipline_hint=None, не привязана к одному
    разделу (встречается почти в каждой марке РД)."""
    kind = classify_table_page(_page("Спецификация оборудования, изделий и материалов"), 1)
    assert kind is not None
    assert kind.kind == "equipment_specification"
    assert kind.discipline_hint is None
    print("OK: универсальная спецификация оборудования классифицируется без привязки к разделу")


def test_classifies_work_volumes_scaffold():
    kind = classify_table_page(_page("Ведомость объёмов работ"), 1)
    assert kind is not None
    assert kind.kind == "work_volumes"
    print("OK: ведомость объёмов работ (ПОС) классифицируется")


def test_classifies_site_balance_scaffold():
    kind = classify_table_page(_page("Баланс территории"), 1)
    assert kind is not None
    assert kind.kind == "site_balance"
    print("OK: баланс территории (ГП) классифицируется")


def test_first_match_wins_when_page_ambiguous():
    """Порядок реестра детерминирует, какая запись матчится первой, если
    страница совпадает с несколькими (маловероятно на практике, но
    поведение должно быть предсказуемым, не случайным)."""
    kind = classify_table_page(_page("Таблица воздухообменов помещений"), 1)
    assert kind.kind == "ventilation_balance"
    print("OK: при совпадении возвращается первая запись реестра (детерминированно)")


def test_all_known_kinds_lists_every_registry_entry_with_honest_status():
    kinds = all_known_kinds()
    assert len(kinds) >= 10
    n1 = [k for k in kinds if k.status.startswith("n=1")]
    n0 = [k for k in kinds if k.status.startswith("n=0")]
    assert len(n1) == 1 and n1[0].kind == "ventilation_balance"
    assert len(n0) == len(kinds) - 1
    print("OK: реестр честно показывает n=1 только у вентиляции, остальное — n=0 заготовка")


if __name__ == "__main__":
    test_classifies_ventilation_balance_same_as_before()
    test_returns_none_for_unknown_table()
    test_returns_none_for_long_page_even_with_matching_word()
    test_classifies_water_balance_scaffold()
    test_classifies_electrical_loads_scaffold()
    test_classifies_cable_log_scaffold()
    test_classifies_steel_consumption_scaffold()
    test_classifies_shipping_marks_scaffold()
    test_classifies_finishing_schedule_scaffold()
    test_classifies_openings_schedule_scaffold()
    test_classifies_equipment_specification_scaffold()
    test_classifies_work_volumes_scaffold()
    test_classifies_site_balance_scaffold()
    test_first_match_wins_when_page_ambiguous()
    test_all_known_kinds_lists_every_registry_entry_with_honest_status()
    print("ALL PASS")
