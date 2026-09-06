"""Блокировка фактов по номеру помещения перед LLM-слоем."""
from __future__ import annotations

from analysis.llm_layer import MAX_TEXT_BLOCKS, _priority_ids, _side_facts
from analysis.models import DocumentSet, ParsedDoc
from analysis.rules.rooms import candidate_rooms, facts_by_room, room_numbers
from documents.schemas import DocKind, Fact, SourceRef, StateKind


def _fact(fact_id: str, fact_type: str, key: str, value, page: int = 1) -> Fact:
    return Fact(id=fact_id, doc_id="D1", fact_type=fact_type, key=key, value=value,
               source=SourceRef(file_id="F1", page=page, bbox=(0, 0, 10, 10)))


def _doc(doc_id: str, state: StateKind, facts: list[Fact]) -> ParsedDoc:
    return ParsedDoc(id=doc_id, file_id=doc_id, object_id="OBJ-X", doc_kind=DocKind.PD,
                     state_kind=state, title=doc_id, facts=facts)


def test_candidate_rooms_is_the_intersection():
    before = DocumentSet(object_id="OBJ-X", state_kind=StateKind.PD, docs=[_doc("PD", StateKind.PD, [
        _fact("f1", "room", "140", {"room_no": "140", "name": "Физического эксперимента"}),
        _fact("f2", "room", "999", {"room_no": "999", "name": "Только в проекте"}),
    ])])
    after = DocumentSet(object_id="OBJ-X", state_kind=StateKind.AS_BUILT,
                        docs=[_doc("ID", StateKind.AS_BUILT, [
        _fact("f3", "room", "140", {"room_no": "140", "name": "моделирования"}),
    ])])
    assert room_numbers(before) == {"140", "999"}
    assert candidate_rooms(before, after) == {"140"}


def test_facts_by_room_collects_mentions_by_token():
    """Факт попадает в группу помещения, если номер встречается как отдельное число."""
    docset = DocumentSet(object_id="OBJ-X", state_kind=StateKind.PD, docs=[_doc("PD", StateKind.PD, [
        _fact("f1", "room", "267", {"room_no": "267", "name": "Раздевальная для МГН"}),
        _fact("f2", "text", "", "Регулятор для системы «тёплый пол» в помещениях 267, 270"),
        _fact("f3", "text", "", "Не относящийся к делу текст без номеров"),
    ])])
    grouped = facts_by_room(docset, {"267"})
    ids = {fact.id for fact, _ in grouped["267"]}
    assert ids == {"f1", "f2"}


def test_room_priority_facts_survive_a_tight_cap():
    """Без приоритета факт о нужном помещении может не попасть под лимит на большом комплекте."""
    filler = [_fact(f"filler-{i}", "text", "", f"Незначащий текст номер {i}" * 3)
             for i in range(MAX_TEXT_BLOCKS + 10)]
    target = _fact("target", "text", "", "Изменена схема отопления помещений МГН 267, 270")
    docset = DocumentSet(object_id="OBJ-X", state_kind=StateKind.PD,
                         docs=[_doc("PD", StateKind.PD, [*filler, target])])

    _, blocks_flat, _ = _side_facts(docset, "before")
    assert not any("267, 270" in b for b in blocks_flat), \
        "без приоритета целевой факт должен теряться под плоским лимитом — иначе тест не показателен"

    priority = _priority_ids(facts_by_room(docset, {"267", "270"}))
    _, blocks_priority, _ = _side_facts(docset, "before", priority)
    assert any("267, 270" in b for b in blocks_priority)
