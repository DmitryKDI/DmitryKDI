import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rooms import extract_room_facts


def test_multiline_table_row_number_name_area_category():
    """Реальная форма 'Экспликации помещений' у PyMuPDF: номер, название и
    площадь почти всегда на отдельных строках (проверено на настоящих
    документах Nadzor_Sample), а не одной строкой, как в packages/documents."""
    text = "144\nЛаборантская тип А\n18.0\nВ3\n147\nЛаборантская тип АВ\n19.6\nВ3"
    facts = extract_room_facts(text)
    assert {"key": "144", "name": "Лаборантская тип А"} in facts
    assert {"key": "147", "name": "Лаборантская тип АВ"} in facts
    print("OK: multi-line экспликация row (number / name / area / category) parsed correctly")


def test_inline_plan_label_number_and_name_on_one_line():
    """Подпись прямо на плане у контура помещения — короче, номер и название
    часто попадают в один текстовый блок PyMuPDF."""
    text = "006.1\nФоркамера\n006\nВенткамера\n002 Коридор"
    facts = extract_room_facts(text)
    keys = {f["key"]: f["name"] for f in facts}
    assert keys["006.1"] == "Форкамера"
    assert keys["006"] == "Венткамера"
    assert keys["002"] == "Коридор"
    print("OK: both multi-line and inline 'number name' forms parsed on the same page")


def test_single_letter_name_rejected_as_noise():
    """Реальный ложный срабатыватель на настоящих документах: марка/ось вида
    '4065\\nА' (обрывок таблицы) не должна попадать в реестр как помещение —
    название из одной буквы никогда не бывает настоящим названием комнаты."""
    text = "4065\nА\n144\nЛаборантская тип А\n18.0"
    facts = extract_room_facts(text)
    keys = [f["key"] for f in facts]
    assert "4065" not in keys, facts
    assert "144" in keys
    print("OK: a bare single-letter continuation is rejected, a real room name is kept")


def test_bare_numbers_without_a_name_produce_no_fact():
    """Номер оси/отметки без кириллического названия следом — не помещение."""
    text = "1\n2\n3\n4500\n1200"
    facts = extract_room_facts(text)
    assert facts == [], facts
    print("OK: standalone numbers with no following room name yield nothing")


if __name__ == "__main__":
    test_multiline_table_row_number_name_area_category()
    test_inline_plan_label_number_and_name_on_one_line()
    test_single_letter_name_rejected_as_noise()
    test_bare_numbers_without_a_name_produce_no_fact()
    print("ALL PASS")
