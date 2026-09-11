"""Автоматическое сопоставление листов ПД и РД/ИД для сравнения.

Сопоставление строится каскадом сигналов, которые уже извлечены из документа:
тип листа -> раздел -> подсистема -> номера помещений/оборудования -> текст.
Номер помещения и позиция оборудования считаются инженерными якорями и
должны перевешивать повторяющуюся лексику штампа и общие слова раздела.

Слабое текстовое совпадение не выдаётся за достоверную пару: оно получает
``matched_by='review'``. Такой лист по-прежнему можно показать vision-модели,
чтобы не потерять покрытие, но интерфейс/отчёт видит, что matching требует
проверки. Позиционный резерв остаётся отдельным ``matched_by='position'``.

Раздел и тип листа приходят уже вычисленными на ``DocumentInput`` — этот
модуль не обращается к LLM и остаётся детерминированным.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .classification import PAGE_KIND_TEXT
from .diffing import jaccard, norm_word
from .subsystem import subsystem_lean

# Нижняя граница, при которой пару вообще имеет смысл рассматривать.
MIN_PAGE_MATCH_SIMILARITY = 0.12
# Ниже этого значения пара сохраняется для покрытия, но явно маркируется
# review: на мини-бенчмарке прежние ложные пары имели score около 0.26-0.34.
CONFIDENT_PAGE_MATCH_SIMILARITY = 0.35
SUBSYSTEM_MISMATCH_PENALTY = 0.4
SUBSYSTEM_MATCH_BONUS = 0.08
# Совпадение инженерного идентификатора должно быть сильнее общих слов.
ROOM_ANCHOR_FLOOR = 0.55
EQUIPMENT_ANCHOR_FLOOR = 0.50


@dataclass
class DocumentInput:
    name: str
    pages: int
    text_facts: list[dict] = field(default_factory=list)  # [{page, text}]
    room_facts: list[dict] = field(default_factory=list)  # [{page, key, name}]
    discipline_code: Optional[str] = None
    page_kinds: dict[int, str] = field(default_factory=dict)  # {page: 'drawing'|'text'}
    # [{page, key, name, parent?, qty?}] — позиции ведомости оборудования (Г.20)
    equipment_facts: list[dict] = field(default_factory=list)
    # [{page, room_key, system_code?, ...}] — числовые значения у номера
    # помещения (баланс-рамка, Г.30 п.1)
    balance_facts: list[dict] = field(default_factory=list)


@dataclass
class _PageRef:
    file_idx: int
    page: int
    tokens: set[str]
    kind: str
    room_keys: set[str] = field(default_factory=set)
    equipment_keys: set[str] = field(default_factory=set)
    lean: Optional[str] = None  # ключ подсистемы из subsystem.subsystem_lean


@dataclass
class PagePair:
    before_file_idx: int
    before_page: int
    after_file_idx: int
    after_page: int
    score: float
    matched_by: str  # 'text' | 'review' | 'position'
    page_kind: str  # 'drawing' | 'text'
    discipline_mismatch: bool = False

    @property
    def needs_review(self) -> bool:
        """Пара выбрана не по достаточно сильному содержательному сигналу."""
        return self.matched_by in {"review", "position"} or self.discipline_mismatch


def _cover_pairs(before_list: list[_PageRef], after_list: list[_PageRef]) -> list[tuple[_PageRef, _PageRef]]:
    """Позиционные пары так, чтобы каждый лист обеих сторон имел покрытие."""
    if not before_list or not after_list:
        return []
    n = max(len(before_list), len(after_list))
    pairs = []
    for i in range(n):
        b = before_list[min(i * len(before_list) // n, len(before_list) - 1)]
        a = after_list[min(i * len(after_list) // n, len(after_list) - 1)]
        pairs.append((b, a))
    return pairs


def page_token_set(entry: DocumentInput, page_no: int) -> set[str]:
    tokens: set[str] = set()
    for fact in entry.text_facts:
        if fact["page"] != page_no:
            continue
        for w in fact["text"].split():
            t = norm_word(w)
            if len(t) > 2:
                tokens.add(t)
    for fact in entry.room_facts:
        if fact["page"] != page_no:
            continue
        if fact.get("key"):
            tokens.add(f"room:{fact['key']}")
        for w in fact["name"].split():
            t = norm_word(w)
            if len(t) > 2:
                tokens.add(t)
    for fact in entry.equipment_facts:
        if fact["page"] != page_no:
            continue
        if fact.get("key"):
            tokens.add(f"equip:{fact['key']}")
        for w in fact["name"].split():
            t = norm_word(w)
            if len(t) > 2:
                tokens.add(t)
    return tokens


def room_key_set(entry: DocumentInput, page_no: int) -> set[str]:
    return {str(f["key"]) for f in entry.room_facts
            if f["page"] == page_no and f.get("key")}


def equipment_key_set(entry: DocumentInput, page_no: int) -> set[str]:
    return {str(f["key"]) for f in entry.equipment_facts
            if f["page"] == page_no and f.get("key")}


def _page_text(entry: DocumentInput, page_no: int) -> str:
    return next((f["text"] for f in entry.text_facts if f["page"] == page_no), "")


def _candidate_score(b: _PageRef, a: _PageRef) -> tuple[float, int, float]:
    """Вернуть (итоговый score, число общих инженерных якорей, text_score).

    Мы не складываем все признаки линейно: на инженерном листе один точный
    номер помещения полезнее десятков общих слов. Поэтому сильный якорь
    задаёт нижнюю границу score, а текст используется для ранжирования там,
    где якорей нет или несколько кандидатов имеют одинаковые якоря.
    """
    text_score = jaccard(b.tokens, a.tokens)
    score = text_score
    anchors = 0

    if b.room_keys and a.room_keys:
        common_rooms = b.room_keys & a.room_keys
        anchors += len(common_rooms)
        room_score = jaccard(b.room_keys, a.room_keys)
        if common_rooms:
            score = max(score, ROOM_ANCHOR_FLOOR + (1.0 - ROOM_ANCHOR_FLOOR) * room_score)

    if b.equipment_keys and a.equipment_keys:
        common_equipment = b.equipment_keys & a.equipment_keys
        anchors += len(common_equipment)
        equipment_score = jaccard(b.equipment_keys, a.equipment_keys)
        if common_equipment:
            score = max(
                score,
                EQUIPMENT_ANCHOR_FLOOR
                + (1.0 - EQUIPMENT_ANCHOR_FLOOR) * equipment_score,
            )

    if b.lean and a.lean:
        if b.lean == a.lean:
            score = min(1.0, score + SUBSYSTEM_MATCH_BONUS)
        else:
            score *= SUBSYSTEM_MISMATCH_PENALTY

    return score, anchors, text_score


def _match_pool(
    before_pages: list[_PageRef],
    after_pages: list[_PageRef],
    before_codes: list[Optional[str]],
    after_codes: list[Optional[str]],
) -> list[PagePair]:
    """Основной каскад matching для одного типа листов."""
    after_code_set = {c for c in after_codes if c}

    candidates: list[tuple[float, int, float, _PageRef, _PageRef]] = []
    for b in before_pages:
        if not b.tokens and not b.room_keys and not b.equipment_keys:
            continue
        b_code = before_codes[b.file_idx]
        gate_by_discipline = bool(b_code) and b_code in after_code_set
        for a in after_pages:
            if not a.tokens and not a.room_keys and not a.equipment_keys:
                continue
            if gate_by_discipline and after_codes[a.file_idx] != b_code:
                continue
            score, anchors, text_score = _candidate_score(b, a)
            if score >= MIN_PAGE_MATCH_SIMILARITY:
                candidates.append((score, anchors, text_score, b, a))

    # Сначала инженерные якоря/score, потом текстовый сигнал. Номера страниц
    # используются лишь последним стабильным tie-breaker, не как доказательство.
    candidates.sort(key=lambda c: (-c[0], -c[1], -c[2], c[3].page, c[4].page))
    used_before: set[tuple[int, int]] = set()
    used_after: set[tuple[int, int]] = set()
    pairs: list[PagePair] = []
    for score, _anchors, _text_score, b, a in candidates:
        b_key = (b.file_idx, b.page)
        a_key = (a.file_idx, a.page)
        if b_key in used_before or a_key in used_after:
            continue
        used_before.add(b_key)
        used_after.add(a_key)
        matched_by = "text" if score >= CONFIDENT_PAGE_MATCH_SIMILARITY else "review"
        pairs.append(PagePair(
            b.file_idx, b.page, a.file_idx, a.page, score, matched_by, b.kind,
        ))

    remaining_before = [b for b in before_pages if (b.file_idx, b.page) not in used_before]
    remaining_after = [a for a in after_pages if (a.file_idx, a.page) not in used_after]

    by_code: dict[str, dict[str, list]] = {}
    leftover_before: list[_PageRef] = []
    for b in remaining_before:
        code = before_codes[b.file_idx]
        if code and code in after_code_set:
            by_code.setdefault(code, {"before": [], "after": []})["before"].append(b)
        else:
            leftover_before.append(b)
    leftover_after: list[_PageRef] = []
    for a in remaining_after:
        code = after_codes[a.file_idx]
        if code and code in by_code:
            by_code[code]["after"].append(a)
        else:
            leftover_after.append(a)

    positional: list[tuple[_PageRef, _PageRef, bool]] = []
    for group in by_code.values():
        gb, ga = group["before"], group["after"]
        if gb and ga:
            for b, a in _cover_pairs(gb, ga):
                positional.append((b, a, False))
        else:
            leftover_before.extend(gb)
            leftover_after.extend(ga)

    if leftover_before and leftover_after:
        for b, a in _cover_pairs(leftover_before, leftover_after):
            b_code = before_codes[b.file_idx]
            a_code = after_codes[a.file_idx]
            mismatch = bool(b_code and a_code and b_code != a_code)
            positional.append((b, a, mismatch))

    for b, a, mismatch in positional:
        pairs.append(PagePair(
            b.file_idx, b.page, a.file_idx, a.page,
            0.0, "position", b.kind, mismatch,
        ))

    return pairs


def match_page_pairs(before_files: list[DocumentInput], after_files: list[DocumentInput]) -> list[PagePair]:
    before_codes = [f.discipline_code for f in before_files]
    after_codes = [f.discipline_code for f in after_files]

    # Файл РД/ИД внутри раздела часто посвящён одной подсистеме целиком.
    # Уровень файла даёт более устойчивый сигнал, чем одна скудная страница.
    after_file_leans = [
        subsystem_lean(" ".join(f["text"] for f in entry.text_facts), code)
        for entry, code in zip(after_files, after_codes)
    ]

    before_pages = [
        _PageRef(
            fi,
            p,
            page_token_set(entry, p),
            entry.page_kinds.get(p, PAGE_KIND_TEXT),
            room_key_set(entry, p),
            equipment_key_set(entry, p),
            subsystem_lean(_page_text(entry, p), before_codes[fi]),
        )
        for fi, entry in enumerate(before_files)
        for p in range(1, entry.pages + 1)
    ]
    after_pages = [
        _PageRef(
            fi,
            p,
            page_token_set(entry, p),
            entry.page_kinds.get(p, PAGE_KIND_TEXT),
            room_key_set(entry, p),
            equipment_key_set(entry, p),
            after_file_leans[fi],
        )
        for fi, entry in enumerate(after_files)
        for p in range(1, entry.pages + 1)
    ]

    pairs: list[PagePair] = []
    kinds = {p.kind for p in before_pages} | {p.kind for p in after_pages}
    for kind in kinds:
        pool_before = [p for p in before_pages if p.kind == kind]
        pool_after = [p for p in after_pages if p.kind == kind]
        if not pool_before or not pool_after:
            continue
        pairs.extend(_match_pool(pool_before, pool_after, before_codes, after_codes))

    return pairs
