import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import PagePair
from app.deep_dive_checklist import DEPTH_TEXT, DEPTH_VISUAL
from app.deep_dive_session import DeepDiveSession


def _pair(before_page, after_page, score):
    return PagePair(0, before_page, 0, after_page, score, "text", "drawing")


def test_current_never_shows_ahead_of_progress():
    """Реальный сбой девятого прогона: список целиком виден заранее, и это
    и есть искушение пойти не по порядку. current() обязан отдавать только
    сегодняшний пункт, не завтрашние."""
    pairs = [_pair(88, 20, 0.338), _pair(99, 16, 0.261)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 88, "key": "142"},
                  {"page": 99, "key": "270"}]
    session = DeepDiveSession.start(pairs, room_facts, anchor_hits={"270"})
    item = session.current()
    # первый пункт — лучшая по скору пара, её первое помещение, а не
    # помещение 270 из якорной, но менее приоритетной пары
    assert item.score == 0.338
    assert item.room_key == "140"
    print("OK: current() указывает на пункт с максимальным скором, а не на якорный")


def test_resolve_is_the_only_way_forward():
    pairs = [_pair(88, 20, 0.5)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 88, "key": "142"}]
    session = DeepDiveSession.start(pairs, room_facts, set())
    first = session.current()
    assert first.room_key == "140"
    # без resolve() current() продолжает отдавать тот же пункт
    assert session.current().room_key == "140"
    session.resolve(DEPTH_TEXT, finding=None)
    assert session.current().room_key == "142"
    print("OK: продвижение возможно только через resolve(), current() не меняется сам по себе")


def test_anchor_priority_only_orders_rooms_within_the_top_pair():
    """Якорь Г.19 не должен переставлять саму очередь пар — только
    помещения внутри уже текущей (в данном случае и так топовой) пары."""
    pairs = [_pair(88, 20, 0.338), _pair(99, 16, 0.261)]
    room_facts = [
        {"page": 88, "key": "140"}, {"page": 88, "key": "142"}, {"page": 88, "key": "314"},
        {"page": 99, "key": "270"},
    ]
    session = DeepDiveSession.start(pairs, room_facts, anchor_hits={"314"})
    # пара 88 (лучший скор) идёт первой целиком; якорное помещение 314
    # идёт первым ВНУТРИ неё, но пара 99 (с якорем 270) всё равно ждёт своей очереди
    seen_pairs = []
    while (item := session.current()) is not None:
        seen_pairs.append(item.score)
        session.resolve(DEPTH_TEXT)
    assert seen_pairs == [0.338, 0.338, 0.338, 0.261]
    print("OK: якорь переупорядочивает помещения внутри пары, не саму очередь пар")


def test_pair_without_rooms_yields_single_whole_sheet_item():
    session = DeepDiveSession.start([_pair(1, 2, 0.5)], [], set())
    item = session.current()
    assert item.room_key is None and item.note
    session.resolve(DEPTH_TEXT)
    assert session.current() is None
    print("OK: пара без помещений в реестре даёт один пункт «лист целиком», не падает")


def test_skip_rest_of_pair_moves_to_next_pair_not_arbitrary_one():
    """Обрезка бюджета честная (Г.10) — помечает недосмотренные помещения,
    но следующий пункт всё равно следующая ПО СКОРУ пара, не любая другая."""
    pairs = [_pair(88, 20, 0.5), _pair(99, 16, 0.3)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 88, "key": "142"},
                  {"page": 99, "key": "270"}]
    session = DeepDiveSession.start(pairs, room_facts, set())
    session.skip_rest_of_pair("бюджет времени")
    nxt = session.current()
    assert nxt.score == 0.3 and nxt.room_key == "270"
    print("OK: skip_rest_of_pair переходит к следующей по скору паре, не произвольно вперёд")


def test_session_exhausted_returns_none():
    session = DeepDiveSession.start([_pair(1, 2, 0.5)], [{"page": 1, "key": "5"}], set())
    session.resolve(DEPTH_TEXT)
    assert session.current() is None
    session.resolve(DEPTH_TEXT)  # повторный вызов после конца — не должен падать
    assert session.current() is None
    print("OK: исчерпанная сессия отдаёт None, повторный resolve() безопасен")


def test_status_separates_visual_crops_from_text_only_checks():
    """Реальный сбой десятого прогона: все 531 пункт «проверены», но почти
    все — текстовой сверкой названия, а нужен был кроп. 531/531 при 2 кропах
    и 531/531 при 200 кропах — разные прогоны, отчёт обязан их различать."""
    pairs = [_pair(88, 20, 0.5)]
    room_facts = [{"page": 88, "key": "140"}, {"page": 88, "key": "142"},
                  {"page": 88, "key": "147"}]
    session = DeepDiveSession.start(pairs, room_facts, set())
    session.resolve(DEPTH_VISUAL, finding="вытяжка отсутствует")
    session.resolve(DEPTH_TEXT)
    session.resolve(DEPTH_TEXT)
    status = session.status()
    assert "кроп: 1" in status, status
    assert "закрыто 3, из них кропом 1, текстом 2" in status, status
    print("OK: status() показывает глубину проверки отдельно от факта закрытия")


def test_resolve_rejects_unknown_depth():
    """Способ закрытия называется явно — молчаливого значения по умолчанию
    быть не должно, иначе всё снова схлопнется в одну галочку."""
    session = DeepDiveSession.start([_pair(1, 2, 0.5)], [{"page": 1, "key": "5"}], set())
    try:
        session.resolve("done")
    except ValueError as e:
        assert "text_only" in str(e) and "visual" in str(e)
    else:
        raise AssertionError("resolve() принял неизвестную глубину проверки")
    # пункт не должен закрыться от неудачного вызова
    assert session.current().room_key == "5"
    print("OK: неизвестная глубина отвергается, пункт остаётся незакрытым")


if __name__ == "__main__":
    test_current_never_shows_ahead_of_progress()
    test_resolve_is_the_only_way_forward()
    test_anchor_priority_only_orders_rooms_within_the_top_pair()
    test_pair_without_rooms_yields_single_whole_sheet_item()
    test_skip_rest_of_pair_moves_to_next_pair_not_arbitrary_one()
    test_session_exhausted_returns_none()
    test_status_separates_visual_crops_from_text_only_checks()
    test_resolve_rejects_unknown_depth()
    print("ALL PASS")
