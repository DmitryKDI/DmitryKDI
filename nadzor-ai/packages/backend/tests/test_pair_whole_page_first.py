"""Лист целиком смотрится первым, а якорь только подсказывает, куда увеличить.

Прежний порядок был обратным: сначала шёл путь по помещениям, потом по
прочим якорям, и лишь `if not items` — просмотр листа целиком. То есть
стоило якорному пути найти хоть что-нибудь, и модель не видела лист целиком
НИКОГДА. Признак сопоставления решал не «куда смотреть раньше», а «смотреть
ли вообще» — то же, что уже было исправлено на отборе пар.

Второе правило этих тестов: «различий нет» — вывод, а не тишина. Он
записывается, только когда лист действительно смотрели, читаемость это
позволяла и бюджет не кончился. Во всех прочих случаях состояние другое, с
названной причиной (Г.10, раздел 13 задания).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.control_pair_runtime as runtime  # noqa: E402
from app.control_pair_candidates import ControlPair  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.matching import DocumentInput  # noqa: E402

CONFIG = LlmConfig(provider="anthropic", api_key="fake")


def _doc(name, rooms, text, pages=1, discipline="ОВ"):
    return DocumentInput(
        name=name,
        pages=pages,
        text_facts=[{"page": page, "text": text} for page in range(1, pages + 1)],
        room_facts=[{"page": 1, "key": room, "name": "Помещение"} for room in rooms],
        discipline_code=discipline,
        page_kinds=dict.fromkeys(range(1, pages + 1), "drawing"),
    )


def _stub(monkeypatch, pairs, *, whole=None, focused=None, generic=None, order=None):
    monkeypatch.setattr(runtime, "run_room_entity_controls", lambda *a, **k: ([], []))
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *a, **k: pairs)
    monkeypatch.setattr(runtime, "visual_change_evidence", lambda *a, **k: {
        "significant": False, "diff_ratio": .001, "raw_diff_ratio": .01,
        "changed_cells": 0, "local_cluster": False, "hot_zone": None,
        "alignment": {"scale": 1.0},
    })

    def _whole(*a, **kw):
        if order is not None:
            order.append("whole")
        return (whole or {"comparable": True, "differences": []})

    def _focused(*a, **kw):
        if order is not None:
            order.append("focused")
        return (focused or ([], [], 1))

    def _generic(*a, **kw):
        if order is not None:
            order.append("generic")
        return (generic or ([], [], 1))

    monkeypatch.setattr(runtime, "semantic_scope_compare", _whole)
    monkeypatch.setattr(runtime, "compare_shared_rooms_focused", _focused)
    monkeypatch.setattr(runtime, "compare_non_room_regions", _generic)


def _run(before, after, **kw):
    return runtime.run_targeted_pair_vision(
        before, after, ["пд.pdf"], ["рд.pdf"], CONFIG, **kw)


def _pages(diagnostics):
    return [x for x in diagnostics if x.get("control_type") == "page_pair"]


def test_whole_page_is_first_even_when_rooms_are_shared(monkeypatch):
    """Пара с общим помещением тоже начинается с листа целиком."""
    order = []
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))],
          focused=([{"change": "изменена трасса", "rooms": ["101"]}], [], 1),
          order=order)

    _run([_doc("пд.pdf", ["101"], "101")], [_doc("рд.pdf", ["101"], "101")])

    assert order[0] == "whole", f"лист целиком обязан быть первым, а не {order}"


def test_anchor_path_no_longer_cancels_the_whole_page_view(monkeypatch):
    """Находка якорного пути не отменяет просмотр листа целиком.

    Ровно это и было сломано: `if not items` означало, что один найденный
    фрагмент закрывал лист от просмотра навсегда.
    """
    order = []
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))],
          focused=([{"change": "изменена трасса", "rooms": ["101"]}], [], 1),
          order=order)

    _run([_doc("пд.pdf", ["101"], "101")], [_doc("рд.pdf", ["101"], "101")])

    assert order.count("whole") == 1


def test_every_pair_is_seen_whole_before_the_first_pair_is_zoomed(monkeypatch):
    """Бюджет увеличений не съедается первой парой (раздел 10 задания)."""
    order = []
    pairs = [ControlPair(0, 1, 0, page, .9, "room_overlap", ("101",))
             for page in (1, 2, 3)]
    _stub(monkeypatch, pairs, whole={
        "comparable": True, "comparability": "medium", "differences": [],
        "candidate_regions": [{"reason": "узел", "rd_bbox_norm": [.2, .2, .6, .6]}],
    }, order=order)

    _run([_doc("пд.pdf", ["101"], "101")],
         [_doc("рд.pdf", ["101"], "101", pages=3)])

    assert order[:3] == ["whole", "whole", "whole"], order


def test_confident_clean_sheet_does_not_spend_zoom_budget(monkeypatch):
    """Увеличение — способ разобрать подозрение, а не ритуал на каждом листе."""
    order = []
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))],
          whole={"comparable": True, "comparability": "high", "differences": []},
          order=order)

    _, diagnostics = _run([_doc("пд.pdf", ["101"], "101")],
                          [_doc("рд.pdf", ["101"], "101")])

    assert order == ["whole"], f"лишние вызовы на чистом листе: {order}"
    assert _pages(diagnostics)[0]["status"] == "compared_no_candidate"


def test_unreadable_sheet_is_not_reported_as_no_difference(monkeypatch):
    """«Не разобрать» и «различий нет» — разные состояния (раздел 13)."""
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))],
          whole={"comparable": False, "comparability": "low", "differences": [],
                 "unclear_reason": "видна только таблица"})

    _, diagnostics = _run([_doc("пд.pdf", ["101"], "101")],
                          [_doc("рд.pdf", ["101"], "101")])

    page = _pages(diagnostics)[0]
    assert page["status"] == "not_compared"
    assert page["unclear_reason"] == "видна только таблица"


def test_provider_failure_is_not_reported_as_no_difference(monkeypatch):
    """Сорванный вызов — не отрицательный результат сравнения."""
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))])

    def boom(*a, **kw):
        raise RuntimeError("нет связи")

    monkeypatch.setattr(runtime, "semantic_scope_compare", boom)

    _, diagnostics = _run([_doc("пд.pdf", ["101"], "101")],
                          [_doc("рд.pdf", ["101"], "101")])

    page = _pages(diagnostics)[0]
    assert page["status"] == "not_compared"
    assert "нет связи" in page["unclear_reason"]


def test_budget_exhaustion_is_named_not_silent(monkeypatch):
    """Пара, до которой не дошёл бюджет, не выглядит проверенной."""
    pairs = [ControlPair(0, 1, 0, page, .9, "room_overlap", ("101",))
             for page in (1, 2)]
    _stub(monkeypatch, pairs)

    _, diagnostics = _run([_doc("пд.pdf", ["101"], "101")],
                          [_doc("рд.pdf", ["101"], "101", pages=2)],
                          max_pairs=1)

    statuses = [x["status"] for x in _pages(diagnostics)]
    assert statuses.count("not_compared") == 1
    skipped = next(x for x in _pages(diagnostics) if x["status"] == "not_compared")
    assert "бюджет" in skipped["unclear_reason"], skipped["unclear_reason"]


def test_model_region_is_zoomed_and_can_confirm_the_difference(monkeypatch):
    """Увеличение идёт по зоне, которую назвала модель (раздел 6)."""
    clips = []
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))])

    def whole(*a, clip=None, **kw):
        clips.append(clip)
        if clip is None:
            return {"comparable": True, "comparability": "medium", "differences": [],
                    "candidate_regions": [{"reason": "узел",
                                           "rd_bbox_norm": [.2, .2, .6, .6],
                                           "priority": "high"}]}
        return {"comparable": True, "comparability": "high",
                "differences": [{"change": "марка установки другая", "rooms": ["101"]}]}

    monkeypatch.setattr(runtime, "semantic_scope_compare", whole)

    signals, diagnostics = _run([_doc("пд.pdf", ["101"], "101")],
                                [_doc("рд.pdf", ["101"], "101")])

    assert clips == [None, (.2, .2, .6, .6)]
    assert _pages(diagnostics)[0]["status"] == "significant"
    assert any(s.domain == "room" and s.key == "101" for s in signals)


def test_model_regions_from_the_pair_path_are_validated(monkeypatch):
    """Координаты от модели — недоверенные данные и на этом пути тоже."""
    clips = []
    _stub(monkeypatch, [ControlPair(0, 1, 0, 1, .9, "room_overlap", ("101",))])

    def whole(*a, clip=None, **kw):
        clips.append(clip)
        return {"comparable": True, "comparability": "medium", "differences": [],
                "candidate_regions": [{"reason": "схлопнута",
                                       "rd_bbox_norm": [.3, .3, .3, .3]}]}

    monkeypatch.setattr(runtime, "semantic_scope_compare", whole)

    _run([_doc("пд.pdf", ["101"], "101")], [_doc("рд.pdf", ["101"], "101")])

    assert clips == [None], "вырожденная зона не должна становиться кропом"


def test_second_region_of_a_pair_waits_for_the_first_region_of_the_rest(monkeypatch):
    """Круги, а не подряд: иначе первая пара выбирает весь бюджет.

    Заодно закрывается вторая зона: обход по кругам не должен «терять»
    зоны, до которых очередь доходит только на втором круге.
    """
    seen = []
    pairs = [ControlPair(0, 1, 0, page, .9, "room_overlap", ("101",))
             for page in (1, 2)]
    _stub(monkeypatch, pairs)

    def whole(before_path, before_page, after_path, after_page, *a, clip=None, **kw):
        seen.append((after_page, clip))
        if clip is not None:
            return {"comparable": True, "comparability": "medium", "differences": []}
        return {"comparable": True, "comparability": "medium", "differences": [],
                "candidate_regions": [
                    {"reason": "первая", "rd_bbox_norm": [.1, .1, .4, .4]},
                    {"reason": "вторая", "rd_bbox_norm": [.5, .5, .9, .9]},
                ]}

    monkeypatch.setattr(runtime, "semantic_scope_compare", whole)

    _run([_doc("пд.pdf", ["101"], "101")],
         [_doc("рд.pdf", ["101"], "101", pages=2)])

    zooms = [(page, clip) for page, clip in seen if clip is not None]
    assert zooms == [
        (1, (.1, .1, .4, .4)),
        (2, (.1, .1, .4, .4)),
        (1, (.5, .5, .9, .9)),
        (2, (.5, .5, .9, .9)),
    ], zooms
