"""Fuse independent control signals into inspector control points.

Triangulation enriches confidence but is not a hard gate for semantic Vision.
A direct semantic observation from Vision is already a valid control point; extra
registry/raster/routing sources only add corroboration.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from .anchors import normalize_room_key, normalize_room_references

CONFIRMED = "confirmed"
CANDIDATE = "candidate"
DEFAULT_MIN_SOURCES = 2

# These sources come from semantic inspection of the actual engineering content.
# They must not be downgraded merely because a cheap independent heuristic did
# not emit a second signal for the same key.
_DIRECT_SEMANTIC_SOURCES = {"vision", "vision_pair"}

_ROOM_SIGNAL_TYPES = {"missing_in_rd", "area_changed"}
_EQUIP_SIGNAL_TYPES = {"missing_in_rd", "qty_changed"}
_ROUTING_FINDING_CATEGORIES = ("retargeted", "connection_count_changed")


@dataclass(frozen=True)
class Signal:
    source: str
    domain: str
    key: str
    detail: str = ""


@dataclass(frozen=True)
class Confirmation:
    domain: str
    key: str
    status: str
    sources: tuple[str, ...] = ()
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_count(self) -> int:
        return len(self.sources)


def _canonical_signal(signal: Signal) -> Signal | None:
    key = str(signal.key or "").strip()
    if signal.domain == "room":
        key = normalize_room_key(key)
    if not key:
        return None
    return Signal(signal.source, signal.domain, key, signal.detail)


def triangulate(signals: Sequence[Signal], min_sources: int = DEFAULT_MIN_SOURCES) -> list[Confirmation]:
    """Group evidence without requiring two sources for semantic Vision.

    Deterministic/registry-only observations still require `min_sources` to be
    called confirmed. A semantic Vision observation is confirmed by itself;
    additional sources remain visible as confidence enrichment.
    """
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for raw in signals:
        signal = _canonical_signal(raw)
        if signal is not None:
            grouped[(signal.domain, signal.key)].append(signal)

    out: list[Confirmation] = []
    for (domain, key), group in grouped.items():
        sources = tuple(sorted({signal.source for signal in group}))
        details = tuple(dict.fromkeys(signal.detail for signal in group if signal.detail))
        has_direct_semantic = any(source in _DIRECT_SEMANTIC_SOURCES for source in sources)
        status = CONFIRMED if has_direct_semantic or len(sources) >= min_sources else CANDIDATE
        out.append(Confirmation(domain, key, status, sources, details))
    return sorted(out, key=lambda item: (item.domain, item.key))


def confirmed_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [item for item in confirmations if item.status == CONFIRMED]


def candidates_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [item for item in confirmations if item.status == CANDIDATE]


def signals_from_room_cross_check(findings) -> list[Signal]:
    out: list[Signal] = []
    for finding in findings:
        if getattr(finding, "finding_type", "") not in _ROOM_SIGNAL_TYPES:
            continue
        key = normalize_room_key(getattr(finding, "room_key", ""))
        if key:
            out.append(Signal("room_registry", "room", key, finding.detail))
    return out


def signals_from_equip_cross_check(findings) -> list[Signal]:
    return [
        Signal("equip_registry", "equipment", str(finding.equip_key), finding.detail)
        for finding in findings
        if getattr(finding, "finding_type", "") in _EQUIP_SIGNAL_TYPES
        and str(getattr(finding, "equip_key", "")).strip()
    ]


def signals_from_routing_diff(diff: dict[str, list[dict]]) -> list[Signal]:
    out: list[Signal] = []
    for category in _ROUTING_FINDING_CATEGORIES:
        for entry in diff.get(category, []):
            key = normalize_room_key(entry.get("room_key", ""))
            if key:
                out.append(Signal("routing", "room", key, category))
    return out


def signals_from_requirement_cross_check(findings) -> list[Signal]:
    out: list[Signal] = []
    for finding in findings:
        if finding.finding_type == "no_code_visual_check_needed":
            for room in normalize_room_references(finding.rooms):
                out.append(Signal("requirement_prose", "room", room, finding.detail))
        elif finding.finding_type == "code_missing_in_rd" and finding.code:
            out.append(Signal("requirement_prose", "requirement_code", str(finding.code), finding.detail))
    return out


def signals_from_visual_requirement_checks(results: Sequence[dict]) -> list[Signal]:
    out: list[Signal] = []
    for result in results:
        if result.get("verdict") != "absent":
            continue
        reason = str(result.get("reason") or "").strip()
        where = str(result.get("where") or "").strip()
        detail = f"{reason} [{where}]" if reason and where else (reason or where)
        for room in normalize_room_references(result.get("rooms") or []):
            out.append(Signal("vision", "room", room, detail))
    return out


def signal_from_vision_verdict(room_key: str, detail: str = "") -> Signal:
    return Signal("vision", "room", normalize_room_key(room_key), detail)
