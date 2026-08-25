"""Автоматическое сопоставление листов ПД и РД/ИД для сравнения.

Порт matchPagePairs из nadzor-browser/app.js — жадное сопоставление листов по
пересечению значимых токенов текста листа (Jaccard), с гейтингом по коду
раздела (см. classification.py) и позиционным резервом для листов, которые
не удалось сопоставить ни по тексту, ни по разделу.

Отличие от браузерной версии: там discipline_code вычислялся внутри самой
функции по textFacts/имени файла. Здесь код раздела приходит уже вычисленным
на DocumentInput — потому что в бэкенде он может быть получен и через
vision-fallback (см. classification.classify_document), а matching.py не
должен знать про LLM/vision вообще, только сопоставлять по готовым данным.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .diffing import jaccard, norm_word

MIN_PAGE_MATCH_SIMILARITY = 0.12


@dataclass
class DocumentInput:
    name: str
    pages: int
    text_facts: list[dict] = field(default_factory=list)  # [{page, text}]
    room_facts: list[dict] = field(default_factory=list)  # [{page, key, name}]
    discipline_code: Optional[str] = None


@dataclass
class _PageRef:
    file_idx: int
    page: int
    tokens: set[str]


@dataclass
class PagePair:
    before_file_idx: int
    before_page: int
    after_file_idx: int
    after_page: int
    score: float
    matched_by: str  # 'text' | 'position'
    discipline_mismatch: bool = False


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
    return tokens


def match_page_pairs(before_files: list[DocumentInput], after_files: list[DocumentInput]) -> list[PagePair]:
    before_codes = [f.discipline_code for f in before_files]
    after_codes = [f.discipline_code for f in after_files]
    after_code_set = {c for c in after_codes if c}

    before_pages = [
        _PageRef(fi, p, page_token_set(entry, p))
        for fi, entry in enumerate(before_files)
        for p in range(1, entry.pages + 1)
    ]
    after_pages = [
        _PageRef(fi, p, page_token_set(entry, p))
        for fi, entry in enumerate(after_files)
        for p in range(1, entry.pages + 1)
    ]

    candidates = []
    for b in before_pages:
        if not b.tokens:
            continue
        b_code = before_codes[b.file_idx]
        gate_by_discipline = bool(b_code) and b_code in after_code_set
        for a in after_pages:
            if not a.tokens:
                continue
            if gate_by_discipline and after_codes[a.file_idx] != b_code:
                continue
            score = jaccard(b.tokens, a.tokens)
            if score >= MIN_PAGE_MATCH_SIMILARITY:
                candidates.append((score, b, a))

    candidates.sort(key=lambda c: -c[0])
    used_before: set[tuple[int, int]] = set()
    used_after: set[tuple[int, int]] = set()
    pairs: list[PagePair] = []
    for score, b, a in candidates:
        b_key = (b.file_idx, b.page)
        a_key = (a.file_idx, a.page)
        if b_key in used_before or a_key in used_after:
            continue
        used_before.add(b_key)
        used_after.add(a_key)
        pairs.append(PagePair(b.file_idx, b.page, a.file_idx, a.page, score, "text"))

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
        n = min(len(gb), len(ga))
        used_b, used_a = set(), set()
        for i in range(n):
            b = gb[(i * len(gb)) // n]
            a = ga[(i * len(ga)) // n]
            used_b.add(id(b))
            used_a.add(id(a))
            positional.append((b, a, False))
        leftover_before.extend(b for b in gb if id(b) not in used_b)
        leftover_after.extend(a for a in ga if id(a) not in used_a)

    n2 = min(len(leftover_before), len(leftover_after))
    for i in range(n2):
        b = leftover_before[(i * len(leftover_before)) // n2]
        a = leftover_after[(i * len(leftover_after)) // n2]
        b_code = before_codes[b.file_idx]
        a_code = after_codes[a.file_idx]
        mismatch = bool(b_code and a_code and b_code != a_code)
        positional.append((b, a, mismatch))

    for b, a, mismatch in positional:
        pairs.append(PagePair(b.file_idx, b.page, a.file_idx, a.page, 0.0, "position", mismatch))

    return pairs
