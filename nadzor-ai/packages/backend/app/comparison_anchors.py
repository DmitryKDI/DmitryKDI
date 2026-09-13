from __future__ import annotations

import re
from typing import Sequence

from .matching import DocumentInput

_AXIS_RE = re.compile(r"(?i)(?:\bось\b|\bоси\b)\s*([А-ЯA-Z]|\d{1,3})")
_ALNUM_RE = re.compile(r"(?<![\w])([А-ЯA-Z]{1,6}\s*[-.]?\s*\d{1,3}(?:\.\d+)?)(?![\w])", re.IGNORECASE)
_SYSTEM_WORD_RE = re.compile(
    r"(?i)(?:система|установка|вентсистема|стояк|щит|агрегат|ветка)\s*[№#:]?\s*"
    r"([А-ЯA-Z]{1,8}\s*[-.]?\s*\d{1,3}(?:\.\d+)?)"
)
_NOISY_PREFIXES = {"Л", "ЛИСТ", "СТР", "М", "ММ", "ГОСТ", "СП", "РИС", "ТАБ", "П", "Р"}


def _page_text(document: DocumentInput, page: int) -> str:
    return "\n".join(
        str(fact.get("text") or "")
        for fact in document.text_facts
        if int(fact.get("page") or 0) == page
    )


def _compact(value: str) -> str:
    return re.sub(r"[\s.\-]+", "", str(value or "").strip()).upper()


def _prefix(value: str) -> str:
    match = re.match(r"([А-ЯA-Z]+)", value.upper())
    return match.group(1) if match else ""


def page_anchor_keys(document: DocumentInput, page: int, *, limit: int = 36) -> set[str]:
    """Extract generic non-room anchors used only for pairing and ROI routing."""
    text = _page_text(document, page)
    anchors: set[str] = set()
    for raw in _AXIS_RE.findall(text):
        value = _compact(raw)
        if value:
            anchors.add(f"axis:{value}")
    explicit = {_compact(raw) for raw in _SYSTEM_WORD_RE.findall(text)}
    generic = {_compact(raw) for raw in _ALNUM_RE.findall(text)}
    for value in sorted(explicit | generic):
        if not value or len(value) > 14:
            continue
        prefix = _prefix(value)
        if not prefix or prefix in _NOISY_PREFIXES:
            continue
        anchors.add(f"equipment_system:{value}")
        if len(anchors) >= limit:
            break
    return anchors


def shared_anchor_keys(before_doc: DocumentInput, before_page: int, after_doc: DocumentInput, after_page: int) -> tuple[str, ...]:
    shared = page_anchor_keys(before_doc, before_page) & page_anchor_keys(after_doc, after_page)
    return tuple(sorted(shared, key=lambda item: (-anchor_weight(item), item)))


def anchor_kind(key: str) -> str:
    return str(key or "").split(":", 1)[0] if ":" in str(key or "") else "text"


def anchor_value(key: str) -> str:
    return str(key or "").split(":", 1)[1] if ":" in str(key or "") else str(key or "")


def anchor_weight(key: str) -> float:
    kind = anchor_kind(key)
    if kind == "axis":
        return 0.85
    if kind == "equipment_system":
        return 0.60
    return 0.35


def anchor_overlap_strength(keys: Sequence[str]) -> float:
    return sum(anchor_weight(key) for key in set(keys))


def anchor_search_terms(key: str) -> list[str]:
    value = anchor_value(key)
    if not value:
        return []
    if anchor_kind(key) == "axis":
        return [f"ось {value}", f"Ось {value}", value]
    return [value]
