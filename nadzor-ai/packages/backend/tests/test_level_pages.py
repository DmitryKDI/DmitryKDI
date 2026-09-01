import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import level_pages
from app.documents import DocumentFacts
from app.level_pages import (
    augment_room_index_with_level_fallback,
    build_level_fallback_index,
    extract_levels,
    level_fallback_candidates,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_extract_levels_finds_positive_and_negative_marks():
    text = "2 этаж +1.750\nЭкспликация помещений\nотметка чистого пола -2.950"
    assert extract_levels(text) == {"+1.750", "-2.950"}


def test_extract_levels_ignores_unrelated_numbers():
    text = "Помещение 270, площадь 12.4 м2, поз. 07.22"
    assert extract_levels(text) == set()


def test_build_level_fallback_index_links_room_to_drawing_page_by_shared_level(monkeypatch):
    """Экспликация 2 этажа (+1.750) содержит помещение 270; отдельный
    drawing-лист того же этажа несёт ту же отметку в тексте, хотя номер
    270 на нём текстом не читается (переведён в кривые) — ровно случай,
    ради которого модуль написан."""
    facts = DocumentFacts(
        name="РД.pdf", pages=3,
        text_facts=[
            {"page": 19, "text": "Экспликация помещений 2 этаж +1.750"},
            {"page": 32, "text": "План 2 этажа +1.750"},
            {"page": 40, "text": "План подвала -2.950"},
        ],
        room_facts=[{"page": 19, "key": "270", "name": "Санузел для МГН"}],
        page_kinds={19: "text", 32: "drawing", 40: "drawing"},
    )

    def fake_extract(path, name):
        return facts

    original = _patch(level_pages, "extract_document_facts", fake_extract)
    try:
        room_levels, level_drawing_pages = build_level_fallback_index(["РД.pdf"])
    finally:
        level_pages.extract_document_facts = original

    assert room_levels["270"] == {"+1.750"}
    assert {"path": "РД.pdf", "page": 32} in level_drawing_pages["+1.750"]
    assert level_drawing_pages.get("-2.950", []) == [{"path": "РД.pdf", "page": 40}]

    candidates = level_fallback_candidates("270", room_levels, level_drawing_pages)
    assert candidates == [{"path": "РД.pdf", "page": 32}]


def test_augment_room_index_appends_fallback_after_existing_entries(monkeypatch):
    """Существующие (найденные по номеру) страницы остаются первыми —
    _candidate_pages должен пробовать их раньше резервных по уровню."""
    facts = DocumentFacts(
        name="РД.pdf", pages=2,
        text_facts=[
            {"page": 19, "text": "2 этаж +1.750"},
            {"page": 32, "text": "План 2 этажа +1.750"},
        ],
        room_facts=[{"page": 19, "key": "270", "name": "Санузел для МГН"}],
        page_kinds={19: "text", 32: "drawing"},
    )
    original = _patch(level_pages, "extract_document_facts", lambda path, name: facts)
    try:
        room_index = {"270": [{"name": "Санузел для МГН", "doc": "РД.pdf", "path": "РД.pdf", "page": 19}]}
        augmented = augment_room_index_with_level_fallback(room_index, ["РД.pdf"])
    finally:
        level_pages.extract_document_facts = original

    assert [e["page"] for e in augmented["270"]] == [19, 32]


def test_augment_room_index_skips_rooms_with_no_known_level():
    """Помещение без экспликации в РД (level_index пуст) не получает
    резервных кандидатов — сравнивать не с чем."""
    room_index: dict[str, list[dict]] = {}
    augmented = augment_room_index_with_level_fallback(room_index, [])
    assert augmented == {}


if __name__ == "__main__":
    test_extract_levels_finds_positive_and_negative_marks()
    test_extract_levels_ignores_unrelated_numbers()
    test_build_level_fallback_index_links_room_to_drawing_page_by_shared_level(None)
    test_augment_room_index_appends_fallback_after_existing_entries(None)
    test_augment_room_index_skips_rooms_with_no_known_level()
    print("ALL PASS")
