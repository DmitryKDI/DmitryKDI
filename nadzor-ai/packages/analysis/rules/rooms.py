"""Группировка фактов по номеру помещения — блокировка перед LLM-слоем.

Номер помещения — ключ сшивки, не зависящий от формы документа: он извлекается
одним и тем же образом (documents.extract._room_facts) и из подписи на плане
ПД, и из строки «Экспликации помещений» РД/ИД. Дорогой семантический слой
получает уже отобранные по номеру помещения кандидатные факты, а не документ
целиком — это и есть блокировка, необходимая для комплектов на сотни листов.
"""
from __future__ import annotations

import re

from documents.schemas import Fact

from analysis.models import DocumentSet, ParsedDoc

_ROOM_TOKEN = re.compile(r"\b\d{3,4}[а-яё]?\b")


def room_numbers(state: DocumentSet | None) -> set[str]:
    """Номера помещений, известные по комплекту состояния."""
    if state is None:
        return set()
    return {f.key for d in state.docs for f in d.facts_of("room") if f.key}


def candidate_rooms(before: DocumentSet | None, after: DocumentSet | None) -> set[str]:
    """Номера помещений, присутствующие в обоих состояниях — есть с чем сравнивать."""
    return room_numbers(before) & room_numbers(after)


def facts_by_room(state: DocumentSet | None,
                  rooms: set[str]) -> dict[str, list[tuple[Fact, ParsedDoc]]]:
    """Факты, относящиеся к номеру помещения.

    Факт относится к помещению, если это сам факт о помещении с этим номером,
    либо номер встречается в его тексте или значении как отдельное число —
    так на чертежах подписаны относящиеся к помещению позиции оборудования.
    """
    grouped: dict[str, list[tuple[Fact, ParsedDoc]]] = {room: [] for room in rooms}
    if state is None or not rooms:
        return grouped
    for doc in state.docs:
        for fact in doc.facts:
            mentioned = {tok for tok in _ROOM_TOKEN.findall(_fact_text(fact)) if tok in rooms}
            if fact.fact_type == "room" and fact.key in rooms:
                mentioned.add(fact.key)
            for room in mentioned:
                grouped[room].append((fact, doc))
    return grouped


def _fact_text(fact: Fact) -> str:
    return fact.value if isinstance(fact.value, str) else str(fact.value)
