from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .anchors import normalize_room_key
from .comparison_anchors import anchor_overlap_strength, anchor_kind, page_anchor_keys
from .matching import DocumentInput, match_page_pairs
from .subsystem import subsystem_lean


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
                        if len(shared_rooms) < 2 and overlap < .20:
                            continue
                        score = max(0.0, min(.99, .50 + .38 * overlap + lean_bonus))
                        matched_by = "room_overlap"
                    else:
                        strength = anchor_overlap_strength(shared_anchors)
                        has_axis = any(anchor_kind(key) == "axis" for key in shared_anchors)
                        # A single weak alphanumeric token is too noisy. One
                        # explicit axis is usable when subsystem context agrees;
                        # otherwise require multiple independent anchors.
                        if strength < 1.10 and not (has_axis and same_lean):
                            continue
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
    return pairs
