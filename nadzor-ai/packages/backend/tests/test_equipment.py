import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.equipment import extract_equipment_facts


def test_multiline_table_row_pos_name_qty():
    text = "14\nВентилятор канальный ВК-100\n2\n15\nКлапан обратный КО-100\n1"
    by_key = {f["key"]: f for f in extract_equipment_facts(text)}
    assert by_key["14"]["name"] == "Вентилятор канальный ВК-100", by_key
    assert by_key["14"]["qty"] == "2", by_key
    assert by_key["15"]["name"] == "Клапан обратный КО-100", by_key
    assert by_key["15"]["qty"] == "1", by_key
    print("OK: строка «поз / название / кол-во» разобрана в реестр оборудования")


def test_child_positions_carry_parent_key():
    """Комплектующие одной установки («14.1» / «14.2») должны нести ссылку
    на родителя — иначе пропавшую дочернюю позицию нельзя отличить от
    независимой находки при проверке по Г.16."""
    text = "14.1\nАгрегат основной ВЦ4-75\n1\n14.2\nАгрегат резервный ВЦ4-75\n1"
    by_key = {f["key"]: f for f in extract_equipment_facts(text)}
    assert by_key["14.1"]["parent"] == "14", by_key
    assert by_key["14.2"]["parent"] == "14", by_key
    print("OK: дочерние позиции хранят код родительской установки")


def test_qty_with_unit_suffix_parsed():
    text = "3\nВоздуховод гибкий\n5 шт."
    by_key = {f["key"]: f for f in extract_equipment_facts(text)}
    assert by_key["3"]["qty"] == "5", by_key
    print("OK: количество с суффиксом «шт.»/«компл.» разбирается как число")


def test_position_without_name_is_not_registered():
    """Голый код без продолжения (обрывок соседней таблицы, номер оси) не
    должен попадать в реестр — как и в rooms.py, число без названия не факт."""
    text = "14\n\n15\nКлапан обратный КО-100\n1"
    keys = [f["key"] for f in extract_equipment_facts(text)]
    assert "14" not in keys, keys
    assert "15" in keys, keys
    print("OK: позиция без названия не регистрируется как оборудование")


def test_table_header_row_is_skipped_not_registered():
    text = "Поз.\nОбозначение\nНаименование\nКол-во\n14\nВентилятор ВК-100\n2"
    keys = [f["key"] for f in extract_equipment_facts(text)]
    assert keys == ["14"], keys
    print("OK: заголовок столбцов таблицы не превращается в позицию оборудования")


def test_room_key_on_same_page_excludes_equipment_position():
    """Реальный сбой первого слепого прогона Г.20: подавляющее большинство
    "позиций оборудования" оказались повторно разобранными номерами
    помещений с той же страницы. room_keys — сигнал приоритета помещения."""
    text = "101\nТамбур\n1\n102\nВентилятор ВК-100\n2"
    facts = extract_equipment_facts(text, room_keys={"101"})
    keys = [f["key"] for f in facts]
    assert "101" not in keys, keys
    assert "102" in keys, keys
    print("OK: позиция с ключом, уже занятым номером помещения, отсеивается")


def test_stray_header_between_position_and_name_is_skipped():
    """На некоторых листах заголовок столбца повторяется на каждой странице
    прямо между кодом позиции и её названием (перенос таблицы) — это не
    часть названия и не должно портить запись."""
    text = "14\nНаименование\nВентилятор ВК-100\n2"
    by_key = {f["key"]: f for f in extract_equipment_facts(text)}
    assert by_key["14"]["name"] == "Вентилятор ВК-100", by_key
    print("OK: заголовок столбца между кодом и названием пропускается, не портит запись")


if __name__ == "__main__":
    test_multiline_table_row_pos_name_qty()
    test_child_positions_carry_parent_key()
    test_qty_with_unit_suffix_parsed()
    test_position_without_name_is_not_registered()
    test_room_key_on_same_page_excludes_equipment_position()
    test_table_header_row_is_skipped_not_registered()
    test_stray_header_between_position_and_name_is_skipped()
    print("ALL PASS")
