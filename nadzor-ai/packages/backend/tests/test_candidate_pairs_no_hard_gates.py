"""Признак сопоставления ранжирует пары, но не решает, сравнивать ли их.

Одному листу ПД соответствует НЕСКОЛЬКО листов РД (Г.6), а жадный матчер
страниц даёт не более одной пары на лист (Г.26). Значит все остальные
кандидаты приходили только со второго прохода — по помещениям и якорям, — и
именно там стоял порог: слабый якорь или малое перекрытие помещений
выбрасывали кандидата совсем. Замер на синтетике: три листа РД с общим
якорем давали ОДНОГО кандидата вместо трёх.

Отсев на этом шаге означает, что смысловое сравнение по такой паре не
выполнится НИКОГДА и причина нигде не видна — routing решал не «куда
смотреть раньше», а «смотреть ли вообще» (Г.10).

Теперь слабый признак понижает место пары в очереди, а сколько кандидатов
взять на один лист ПД, решает бюджет просмотра. Эти тесты закрывают именно
границу «понижено в очереди» против «выброшено».
"""
from app.control_pair_candidates import (
    TIER_MEDIUM,
    TIER_STRONG,
    TIER_WEAK,
    TOP_K_STRONG_PAIRS,
    TOP_K_WEAK_PAIRS,
    candidate_pairs,
)
from app.matching import DocumentInput


def _doc(name, pages, rooms_by_page, text_by_page, discipline="ОВ"):
    return DocumentInput(
        name=name,
        pages=pages,
        text_facts=[{"page": page, "text": text_by_page.get(page, "")}
                    for page in range(1, pages + 1)],
        room_facts=[{"page": page, "key": room, "name": "Помещение"}
                    for page, rooms in rooms_by_page.items() for room in rooms],
        discipline_code=discipline,
        page_kinds={page: "drawing" for page in range(1, pages + 1)},
    )


def test_single_weak_anchor_reaches_every_matching_sheet():
    """Слабый якорь — не повод оставить один лист из трёх.

    Жадный матчер отдаёт только первый лист; остальные два появляются
    исключительно вторым проходом, где раньше стоял порог силы якоря.
    """
    before = _doc("ПД.pdf", 1, {}, {1: "установка В1"})
    after = _doc("РД.pdf", 3, {}, {page: "установка В1" for page in (1, 2, 3)})

    pairs = candidate_pairs([before], [after])

    assert sorted(pair.after_page for pair in pairs) == [1, 2, 3], (
        "листы РД с тем же якорем не дошли до сравнения")
    assert {pair.evidence_tier for pair in pairs} == {TIER_WEAK}
    assert pairs[0].shared_anchors == ("equipment_system:В1",)


def test_small_room_overlap_reaches_every_matching_sheet():
    """Совпало одно помещение из многих — тоже кандидат на каждом листе."""
    before = _doc("ПД.pdf", 1, {1: [str(100 + n) for n in range(10)]}, {})
    after = _doc("РД.pdf", 2, {1: ["100"] + [str(300 + n) for n in range(9)],
                               2: ["101"] + [str(400 + n) for n in range(9)]}, {})

    pairs = candidate_pairs([before], [after])

    assert sorted(pair.after_page for pair in pairs) == [1, 2], (
        "лист с малым перекрытием помещений выброшен")
    assert {pair.evidence_tier for pair in pairs} == {TIER_STRONG}


def test_several_independent_anchors_rank_above_a_single_one():
    """Сильнее признак — выше место в очереди; оба остаются кандидатами."""
    before = _doc("ПД.pdf", 1, {}, {1: "установка В1 установка К2"})
    after = _doc("РД.pdf", 2, {}, {1: "установка В1", 2: "установка В1 установка К2"})

    pairs = candidate_pairs([before], [after])

    by_page = {pair.after_page: pair for pair in pairs}
    assert set(by_page) == {1, 2}, "ни одна из пар не должна пропасть"
    assert by_page[2].evidence_tier == TIER_MEDIUM
    assert by_page[1].evidence_tier == TIER_WEAK
    assert pairs.index(by_page[2]) < pairs.index(by_page[1])


def test_weak_evidence_keeps_more_candidates_than_strong_evidence():
    """Чем менее известно, который лист верный, тем больше кандидатов нужно.

    Глубина — бюджет просмотра, а не граница истины: уверенное совпадение по
    помещениям не нуждается в пяти кандидатах, единственный слабый якорь
    нуждается.
    """
    pages = 8
    weak_before = _doc("ПД.pdf", 1, {}, {1: "установка В1"})
    weak_after = _doc("РД.pdf", pages, {}, {page: "установка В1"
                                            for page in range(1, pages + 1)})
    weak_pairs = candidate_pairs([weak_before], [weak_after])

    strong_before = _doc("ПД.pdf", 1, {1: ["100", "101"]}, {})
    strong_after = _doc("РД.pdf", pages, {page: ["100", "101"]
                                          for page in range(1, pages + 1)}, {})
    strong_pairs = candidate_pairs([strong_before], [strong_after])

    assert len(weak_pairs) == TOP_K_WEAK_PAIRS
    assert len(strong_pairs) == TOP_K_STRONG_PAIRS
    assert len(weak_pairs) > len(strong_pairs)


def test_budget_is_counted_per_pd_sheet_not_per_run():
    """Лимит на лист ПД: второй лист не остаётся без кандидатов из-за первого."""
    before = _doc("ПД.pdf", 2, {1: ["100", "101"], 2: ["200", "201"]}, {})
    after = _doc("РД.pdf", 4, {1: ["100", "101"], 2: ["100", "101"],
                               3: ["200", "201"], 4: ["200", "201"]}, {})

    pairs = candidate_pairs([before], [after])

    assert {pair.before_page for pair in pairs} == {1, 2}, "лист ПД остался без кандидатов"
