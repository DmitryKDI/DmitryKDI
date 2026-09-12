from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .anchors import normalize_room_key
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


def candidate_pairs(before_docs: Sequence[DocumentInput], after_docs: Sequence[DocumentInput]) -> list[ControlPair]:
    by_key = {}
    for pair in match_page_pairs(list(before_docs), list(after_docs)):
        if pair.page_kind != "drawing" or pair.discipline_mismatch:
            continue
        before_doc, after_doc = before_docs[pair.before_file_idx], after_docs[pair.after_file_idx]
        shared = room_keys(before_doc, pair.before_page) & room_keys(after_doc, pair.after_page)
        key = (pair.before_file_idx, pair.before_page, pair.after_file_idx, pair.after_page)
        by_key[key] = ControlPair(*key, float(pair.score), pair.matched_by, tuple(sorted(shared)))

    after_leans = [subsystem_lean(document_text(doc), doc.discipline_code) for doc in after_docs]
    for bi, before_doc in enumerate(before_docs):
        for bp in drawing_pages(before_doc):
            before_rooms = room_keys(before_doc, bp)
            if not before_rooms:
                continue
            before_lean = subsystem_lean(page_text(before_doc, bp), before_doc.discipline_code)
            for ai, after_doc in enumerate(after_docs):
                if before_doc.discipline_code and after_doc.discipline_code and before_doc.discipline_code != after_doc.discipline_code:
                    continue
                for ap in drawing_pages(after_doc):
                    after_rooms = room_keys(after_doc, ap)
                    shared = before_rooms & after_rooms
                    if not shared:
                        continue
                    overlap = len(shared) / max(1, min(len(before_rooms), len(after_rooms)))
                    if len(shared) < 2 and overlap < .20:
                        continue
                    lean_bonus = .12 if before_lean and after_leans[ai] and before_lean == after_leans[ai] else 0.0
                    score = max(0.0, min(.99, .50 + .38 * overlap + lean_bonus))
                    key = (bi, bp, ai, ap)
                    item = ControlPair(bi, bp, ai, ap, score, "room_overlap", tuple(sorted(shared)))
                    if key not in by_key or item.score > by_key[key].score:
                        by_key[key] = item

    pairs = list(by_key.values())
    pairs.sort(key=lambda x: (-len(x.shared_rooms), -x.score, x.before_file_idx, x.before_page, x.after_file_idx, x.after_page))
    return pairs
