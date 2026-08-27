import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import PagePair
from app.deep_dive_checklist import (
    DEPTH_TEXT,
    DEPTH_VISUAL,
    build_deep_dive_checklist,
    render_checklist_markdown,
)


def _pair(before_page, after_page, score):
    return PagePair(0, before_page, 0, after_page, score, "text", "drawing")


def test_pairs_ordered_by_score_regardless_of_input_order():
    """Реальный сбой восьмого прогона: пара с текстовым якорем разбиралась
    раньше пары с максимальным скором. Сортировка — обязанность самой
    функции, не вызывающего кода, чтобы порядок нельзя было перепутать."""
    pairs = [_pair(99, 16, 0.261), _pair(88, 20, 0.338), _pair(176, 575, 0.258)]
    entries = build_deep_dive_checklist(pairs, [], set())
    scores = [e.pair.score for e in entries]
    assert scores == [0.338, 0.261, 0.258], scores
    print("OK: пары идут строго по убыванию скора независимо от порядка на входе")


def test_anchor_rooms_prioritized_within_pair_not_across_pairs():
    """Приоритет Г.19 — только порядок ВНУТРИ пары. Пара с якорем всё равно
    идёт после пары с более высоким скором, а не перед ней (граница между
    приёмом №4 и приёмом №5)."""
    pairs = [_pair(88, 20, 0.338), _pair(99, 16, 0.261)]
    room_facts = [
        {"page": 88, "key": "140"}, {"page": 88, "key": "142"}, {"page": 88, "key": "314"},
        {"page": 99, "key": "270"}, {"page": 99, "key": "272"},
    ]
    entries = build_deep_dive_checklist(pairs, room_facts, anchor_hits={"270"})
    # пара 88 (лучший скор) всё равно первая, даже без якоря внутри неё
    assert entries[0].pair.before_page == 88
    assert entries[1].pair.before_page == 99
    # внутри второй пары якорная комната идёт первой
    keys_in_order = [r.room_key for r in entries[1].rooms]
    assert keys_in_order[0] == "270"
    assert entries[1].rooms[0].priority == "anchor"
    print("OK: якорь переставляет помещения внутри пары, но не сами пары")


def test_progress_counter_reflects_checked_state_and_depth():
    """Счётчик показывает не только сколько пунктов закрыто, но и сколько
    из них закрыто настоящим кропом (Г.25) — иначе «проверено» означает
    два разных по глубине действия в одной цифре."""
    pairs = [_pair(88, 20, 0.338)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 88, "key": "142"}]
    entries = build_deep_dive_checklist(pairs, room_facts, set())
    assert entries[0].progress == "0/2 (кроп: 0)"
    entries[0].rooms[0].checked = True
    entries[0].rooms[0].depth = DEPTH_TEXT
    assert entries[0].progress == "1/2 (кроп: 0)"
    entries[0].rooms[1].checked = True
    entries[0].rooms[1].depth = DEPTH_VISUAL
    assert entries[0].progress == "2/2 (кроп: 1)"
    print("OK: счётчик прогресса различает текстовую сверку и визуальный кроп")


def test_pair_with_no_room_facts_gets_empty_checklist():
    """Лист без номеров в реестре (компактная схема, Г.22) не должен падать
    с ошибкой — просто пустой перечень, кроп листа целиком."""
    entries = build_deep_dive_checklist([_pair(1, 2, 0.5)], [], set())
    assert entries[0].rooms == []
    print("OK: пара без номеров помещений даёт пустой, а не падающий чек-лист")


def test_render_markdown_lists_pairs_in_order_with_checkboxes():
    pairs = [_pair(88, 20, 0.338), _pair(99, 16, 0.261)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 99, "key": "270"}]
    md = render_checklist_markdown(build_deep_dive_checklist(pairs, room_facts, {"270"}))
    assert md.index("score=0.338") < md.index("score=0.261")
    assert "[ ] 140" in md
    assert "[ ] 270 (якорь Г.19)" in md
    print("OK: markdown-рендер сохраняет порядок пар и помечает якорные помещения")


if __name__ == "__main__":
    test_pairs_ordered_by_score_regardless_of_input_order()
    test_anchor_rooms_prioritized_within_pair_not_across_pairs()
    test_progress_counter_reflects_checked_state_and_depth()
    test_pair_with_no_room_facts_gets_empty_checklist()
    test_render_markdown_lists_pairs_in_order_with_checkboxes()
    print("ALL PASS")
