from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

from .anchors import normalize_room_key
from .comparison_anchors import anchor_overlap_strength, page_anchor_keys
from .matching import DocumentInput, match_page_pairs
from .subsystem import subsystem_lean


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


# Сколько кандидатных листов РД оставлять НА ОДИН лист ПД. Это бюджет
# просмотра, а не граница истины: чем слабее признак сопоставления, тем
# больше кандидатов нужно оставить, потому что неизвестно, который верный.
# Обратная зависимость намеренная — уверенное совпадение не требует пяти
# кандидатов, а неуверенное не должно схлопываться до одного.
TOP_K_STRONG_PAIRS = _int_env("NADZOR_TOPK_STRONG_PAIRS", 2, 1, 12)
TOP_K_MEDIUM_PAIRS = _int_env("NADZOR_TOPK_MEDIUM_PAIRS", 3, 1, 12)
TOP_K_WEAK_PAIRS = _int_env("NADZOR_TOPK_WEAK_PAIRS", 5, 1, 12)

# Вид признака сопоставления — структурный факт (что именно совпало), а не
# число с подобранной границей. Помещение — сильнейший якорь (Г.5);
# несколько независимых якорей — средний; единственный якорь — слабый.
TIER_STRONG = "room"
TIER_MEDIUM = "anchors"
TIER_WEAK = "single_anchor"

_TIER_BUDGET = {
    TIER_STRONG: TOP_K_STRONG_PAIRS,
    TIER_MEDIUM: TOP_K_MEDIUM_PAIRS,
    TIER_WEAK: TOP_K_WEAK_PAIRS,
}
_TIER_ORDER = {TIER_STRONG: 0, TIER_MEDIUM: 1, TIER_WEAK: 2}


@dataclass(frozen=True)
class ControlPair:
    before_file_idx: int
    before_page: int
    after_file_idx: int
    after_page: int
    score: float
    matched_by: str
    shared_rooms: tuple[str, ...] = ()
    shared_anchors: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.before_file_idx}:{self.before_page}->{self.after_file_idx}:{self.after_page}"

    @property
    def evidence_tier(self) -> str:
        """Чем пара подтверждена: помещением, несколькими якорями, одним.

        Используется ТОЛЬКО чтобы решить, сколько кандидатов оставить на лист
        ПД, и как метка в диагностике. Сравнение по существу от вида признака
        не зависит и не отменяется им.
        """
        if self.shared_rooms:
            return TIER_STRONG
        return TIER_MEDIUM if len(self.shared_anchors) >= 2 else TIER_WEAK


def room_keys(document: DocumentInput, page: int) -> set[str]:
    out = set()
    for fact in document.room_facts:
        if int(fact.get("page") or 0) == page:
            key = normalize_room_key(fact.get("key", ""))
            if key:
                out.add(key)
    return out


def page_text(document: DocumentInput, page: int) -> str:
    return "\n".join(str(f.get("text") or "") for f in document.text_facts if int(f.get("page") or 0) == page)


def document_text(document: DocumentInput) -> str:
    return "\n".join(str(f.get("text") or "") for f in document.text_facts)


def drawing_pages(document: DocumentInput) -> list[int]:
    return [p for p in range(1, document.pages + 1) if document.page_kinds.get(p) == "drawing"]


def _shared_anchor_tuple(before_doc: DocumentInput, before_page: int, after_doc: DocumentInput, after_page: int) -> tuple[str, ...]:
    shared = page_anchor_keys(before_doc, before_page) & page_anchor_keys(after_doc, after_page)
    return tuple(sorted(shared, key=lambda key: (-anchor_overlap_strength([key]), key)))


def candidate_pairs(before_docs: Sequence[DocumentInput], after_docs: Sequence[DocumentInput]) -> list[ControlPair]:
    """Build generic drawing candidates using rooms first and other anchors second.

    Room anchors remain strongest, but drawings without room exposition can be
    paired through shared grid axes or engineering system/equipment labels.
    Anchors only route comparison; they are never interpreted as a finding.
    """
    by_key = {}
    for pair in match_page_pairs(list(before_docs), list(after_docs)):
        if pair.page_kind != "drawing" or pair.discipline_mismatch:
            continue
        before_doc, after_doc = before_docs[pair.before_file_idx], after_docs[pair.after_file_idx]
        shared_rooms = room_keys(before_doc, pair.before_page) & room_keys(after_doc, pair.after_page)
        shared_anchors = _shared_anchor_tuple(before_doc, pair.before_page, after_doc, pair.after_page)
        key = (pair.before_file_idx, pair.before_page, pair.after_file_idx, pair.after_page)
        by_key[key] = ControlPair(
            *key,
            float(pair.score),
            pair.matched_by,
            tuple(sorted(shared_rooms)),
            shared_anchors,
        )

    after_leans = [subsystem_lean(document_text(doc), doc.discipline_code) for doc in after_docs]
    for bi, before_doc in enumerate(before_docs):
        for bp in drawing_pages(before_doc):
            before_rooms = room_keys(before_doc, bp)
            before_anchors = page_anchor_keys(before_doc, bp)
            if not before_rooms and not before_anchors:
                continue
            before_lean = subsystem_lean(page_text(before_doc, bp), before_doc.discipline_code)
            for ai, after_doc in enumerate(after_docs):
                if before_doc.discipline_code and after_doc.discipline_code and before_doc.discipline_code != after_doc.discipline_code:
                    continue
                same_lean = bool(before_lean and after_leans[ai] and before_lean == after_leans[ai])
                lean_bonus = .12 if same_lean else 0.0
                for ap in drawing_pages(after_doc):
                    after_rooms = room_keys(after_doc, ap)
                    after_anchors = page_anchor_keys(after_doc, ap)
                    shared_rooms = before_rooms & after_rooms
                    shared_anchors = tuple(sorted(before_anchors & after_anchors))
                    if not shared_rooms and not shared_anchors:
                        continue

                    if shared_rooms:
                        overlap = len(shared_rooms) / max(1, min(len(before_rooms), len(after_rooms)))
                        score = max(0.0, min(.99, .50 + .38 * overlap + lean_bonus))
                        matched_by = "room_overlap"
                    else:
                        strength = anchor_overlap_strength(shared_anchors)
                        # Слабое совпадение понижает МЕСТО пары в очереди, но
                        # не выбрасывает её: отсев требует порога, порядок —
                        # нет, а сколько кандидатов взять, решает бюджет
                        # просмотра (`_top_k_per_before_page`). Раньше здесь
                        # стоял порог силы якоря, и пара с единственным
                        # слабым признаком не доходила до сравнения вовсе —
                        # то есть routing решал не «куда смотреть раньше», а
                        # «смотреть ли вообще». Формула веса не менялась.
                        score = max(0.0, min(.94, .42 + min(.34, .11 * strength) + lean_bonus))
                        matched_by = "anchor_overlap"

                    key = (bi, bp, ai, ap)
                    item = ControlPair(
                        bi,
                        bp,
                        ai,
                        ap,
                        score,
                        matched_by,
                        tuple(sorted(shared_rooms)),
                        tuple(sorted(shared_anchors, key=lambda anchor: (-anchor_overlap_strength([anchor]), anchor))),
                    )
                    if key not in by_key or item.score > by_key[key].score:
                        by_key[key] = item

    pairs = list(by_key.values())
    pairs.sort(
        key=lambda x: (
            -len(x.shared_rooms),
            -anchor_overlap_strength(x.shared_anchors),
            -x.score,
            x.before_file_idx,
            x.before_page,
            x.after_file_idx,
            x.after_page,
        )
    )
    return _top_k_per_before_page(pairs)


def _top_k_per_before_page(pairs: Sequence[ControlPair]) -> list[ControlPair]:
    """Оставить на каждый лист ПД столько кандидатов, сколько нужно бюджету.

    Одному листу ПД соответствует несколько листов РД (Г.6), и ни один
    признак сопоставления не даёт права оставить ровно одного кандидата:
    чем слабее признак, тем менее известно, который из них верный. Поэтому
    глубина берётся по ЛУЧШЕМУ найденному для этого листа виду признака —
    уверенное совпадение по помещениям не нуждается в пяти кандидатах,
    единственный слабый якорь нуждается.

    Порядок внутри листа сохраняется прежний (он задан вызывающей
    сортировкой), поэтому обрезается только хвост, а не середина.
    """
    best_tier: dict[tuple[int, int], str] = {}
    for pair in pairs:
        page_key = (pair.before_file_idx, pair.before_page)
        tier = pair.evidence_tier
        current = best_tier.get(page_key)
        if current is None or _TIER_ORDER[tier] < _TIER_ORDER[current]:
            best_tier[page_key] = tier

    kept: list[ControlPair] = []
    taken: dict[tuple[int, int], int] = {}
    for pair in pairs:
        page_key = (pair.before_file_idx, pair.before_page)
        budget = _TIER_BUDGET[best_tier[page_key]]
        if taken.get(page_key, 0) >= budget:
            continue
        taken[page_key] = taken.get(page_key, 0) + 1
        kept.append(pair)
    return kept
