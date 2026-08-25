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

Второй, независимый от раздела гейтинг — по типу листа (чертёж/текст, см.
classification.classify_page_kind). Объём ПД и РД/ИД внутри одного раздела
почти никогда не совпадает именно потому, что в один PDF подшиты вперемешку
сами чертежи и текстовые приложения (акты, спецификации, содержание тома) —
сравнивать чертёж с текстовым актом визуально бессмысленно. Поэтому перед
сопоставлением листы делятся на два независимых пула по типу, и весь
алгоритм (текстовое сопоставление + позиционный резерв) прогоняется отдельно
внутри каждого пула — чертежи сравниваются только с чертежами, текст только
с текстом.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .classification import PAGE_KIND_TEXT
from .diffing import jaccard, norm_word
from .subsystem import subsystem_lean

MIN_PAGE_MATCH_SIMILARITY = 0.12
SUBSYSTEM_MISMATCH_PENALTY = 0.4


@dataclass
class DocumentInput:
    name: str
    pages: int
    text_facts: list[dict] = field(default_factory=list)  # [{page, text}]
    room_facts: list[dict] = field(default_factory=list)  # [{page, key, name}]
    discipline_code: Optional[str] = None
    page_kinds: dict[int, str] = field(default_factory=dict)  # {page: 'drawing'|'text'}


@dataclass
class _PageRef:
    file_idx: int
    page: int
    tokens: set[str]
    kind: str
    room_keys: set[str] = field(default_factory=set)
    lean: Optional[str] = None  # subsystem.subsystem_lean — 'вент' | 'тепл' | None


@dataclass
class PagePair:
    before_file_idx: int
    before_page: int
    after_file_idx: int
    after_page: int
    score: float
    matched_by: str  # 'text' | 'position'
    page_kind: str  # 'drawing' | 'text'
    discipline_mismatch: bool = False


def _cover_pairs(before_list: list[_PageRef], after_list: list[_PageRef]) -> list[tuple[_PageRef, _PageRef]]:
    """Позиционные пары так, чтобы КАЖДЫЙ лист с обеих сторон попал хотя бы в
    одну пару — а не так, как раньше: пар получалось min(before, after), и
    если, скажем, РД (712 листов) вдвое объёмнее ПД (177), лишние ~535
    листов РД молча не проверялись вообще (реальный случай, найденный на
    настоящих документах пользователя). Меньшая сторона переиспользуется
    пропорционально — качество такой пары ниже текстовой, но лист хотя бы
    попадает на визуальную проверку, а не пропадает."""
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
    return tokens


def room_key_set(entry: DocumentInput, page_no: int) -> set[str]:
    return {f["key"] for f in entry.room_facts if f["page"] == page_no and f.get("key")}


def _page_text(entry: DocumentInput, page_no: int) -> str:
    return next((f["text"] for f in entry.text_facts if f["page"] == page_no), "")


def _match_pool(
    before_pages: list[_PageRef],
    after_pages: list[_PageRef],
    before_codes: list[Optional[str]],
    after_codes: list[Optional[str]],
) -> list[PagePair]:
    """Основной алгоритм сопоставления (текстовое + позиционный резерв),
    независимый от типа листа — вызывается отдельно для пула чертежей и
    отдельно для пула текстовых листов."""
    after_code_set = {c for c in after_codes if c}

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
            if b.room_keys and a.room_keys:
                # Номер помещения — куда более специфичный сигнал, чем общая
                # лексика листа (заголовки штампа, названия систем и т.п.
                # повторяются на сотнях листов раздела): экспликация листа с
                # десятками случайных общих слов может набрать балл выше, чем
                # лист, где буквально совпадает то самое помещение, где
                # находится нарушение (см. CLAUDE.md — реальный случай на
                # Nadzor_Sample). Поэтому берём лучший из двух сигналов, а не
                # смешиваем — один хороший сигнал не должен тонуть в другом.
                score = max(score, jaccard(b.room_keys, a.room_keys))
            if b.lean and a.lean and b.lean != a.lean:
                # Оба тома одного раздела ОВ, но по словам явно разные
                # подсистемы (вентиляция/отопление) — не блокируем совсем
                # (эвристика по словам ненадёжна на 100%), но совпадение
                # номеров помещений в общем техническом подполье не должно
                # перевешивать явный текстовый признак другой подсистемы.
                score *= SUBSYSTEM_MISMATCH_PENALTY
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
        pairs.append(PagePair(b.file_idx, b.page, a.file_idx, a.page, score, "text", b.kind))

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
            # Одна из сторон пуста — сравнивать не с чем внутри этого раздела,
            # отдаём в общий резерв, а не молча теряем эти листы.
            leftover_before.extend(gb)
            leftover_after.extend(ga)

    if leftover_before and leftover_after:
        for b, a in _cover_pairs(leftover_before, leftover_after):
            b_code = before_codes[b.file_idx]
            a_code = after_codes[a.file_idx]
            mismatch = bool(b_code and a_code and b_code != a_code)
            positional.append((b, a, mismatch))

    for b, a, mismatch in positional:
        pairs.append(PagePair(b.file_idx, b.page, a.file_idx, a.page, 0.0, "position", b.kind, mismatch))

    return pairs


def match_page_pairs(before_files: list[DocumentInput], after_files: list[DocumentInput]) -> list[PagePair]:
    before_codes = [f.discipline_code for f in before_files]
    after_codes = [f.discipline_code for f in after_files]

    # Файл РД/ИД внутри раздела ОВ почти всегда посвящён одной подсистеме
    # целиком (см. subsystem.py) — уровень файла даёт достаточно текста для
    # надёжного сигнала, отдельная страница может быть слишком скудной.
    after_file_leans = [subsystem_lean(" ".join(f["text"] for f in entry.text_facts)) for entry in after_files]

    before_pages = [
        _PageRef(fi, p, page_token_set(entry, p), entry.page_kinds.get(p, PAGE_KIND_TEXT),
                 room_key_set(entry, p), subsystem_lean(_page_text(entry, p)))
        for fi, entry in enumerate(before_files)
        for p in range(1, entry.pages + 1)
    ]
    after_pages = [
        _PageRef(fi, p, page_token_set(entry, p), entry.page_kinds.get(p, PAGE_KIND_TEXT),
                 room_key_set(entry, p), after_file_leans[fi])
        for fi, entry in enumerate(after_files)
        for p in range(1, entry.pages + 1)
    ]

    pairs: list[PagePair] = []
    kinds = {p.kind for p in before_pages} | {p.kind for p in after_pages}
    for kind in kinds:
        pool_before = [p for p in before_pages if p.kind == kind]
        pool_after = [p for p in after_pages if p.kind == kind]
        if not pool_before or not pool_after:
            continue  # нечего сравнивать в этом пуле вообще (например, только чертежи с обеих сторон)
        pairs.extend(_match_pool(pool_before, pool_after, before_codes, after_codes))

    return pairs
