"""Правило триангуляции независимых источников.

Сигнал — это не готовое нарушение, а независимое указание одного модуля на
возможное расхождение. Находка становится ``confirmed`` только когда минимум
два разных источника указывают на один и тот же ключ. Одиночные сигналы
остаются ``candidate`` и идут в очередь инспектору.

Важно: не каждый сырой diff обязан становиться сигналом. Реестры помещений
и оборудования намеренно сохраняют широкий диагностический вывод, но в
триангуляцию допускаются только достаточно сильные категории. Это защищает
точки контроля от шума парсинга и от ситуации, когда сотни новых позиций РД
выглядят как сотни потенциальных нарушений.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

CONFIRMED = "confirmed"
CANDIDATE = "candidate"
DEFAULT_MIN_SOURCES = 2

# Слабые категории остаются в исходных diagnostics, но не повышают
# уверенность триангуляции без отдельного подтверждения.
_ROOM_SIGNAL_TYPES = {"missing_in_rd", "area_changed"}
_EQUIP_SIGNAL_TYPES = {"missing_in_rd", "qty_changed"}


@dataclass(frozen=True)
class Signal:
    """Один независимый сигнал по ключу ``(domain, key)``."""

    source: str
    domain: str
    key: str
    detail: str = ""


@dataclass(frozen=True)
class Confirmation:
    """Итог триангуляции по одному ``(domain, key)``."""

    domain: str
    key: str
    status: str
    sources: tuple[str, ...] = ()
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_count(self) -> int:
        return len(self.sources)


def triangulate(signals: Sequence[Signal], min_sources: int = DEFAULT_MIN_SOURCES) -> list[Confirmation]:
    """Сгруппировать сигналы и посчитать именно РАЗНЫЕ источники."""

    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for signal in signals:
        grouped[(signal.domain, signal.key)].append(signal)

    out: list[Confirmation] = []
    for (domain, key), group in grouped.items():
        sources = tuple(sorted({signal.source for signal in group}))
        details = tuple(signal.detail for signal in group if signal.detail)
        status = CONFIRMED if len(sources) >= min_sources else CANDIDATE
        out.append(
            Confirmation(
                domain=domain,
                key=key,
                status=status,
                sources=sources,
                details=details,
            )
        )
    out.sort(key=lambda item: (item.domain, item.key))
    return out


def confirmed_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [item for item in confirmations if item.status == CONFIRMED]


def candidates_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [item for item in confirmations if item.status == CANDIDATE]


# --------------------------------------------------------------------------
# Адаптеры источников
# --------------------------------------------------------------------------

def signals_from_room_cross_check(findings) -> list[Signal]:
    """Сильные сигналы из реестра помещений.

    ``name_changed`` сейчас не участвует в автоматическом подтверждении:
    этот тип особенно чувствителен к обрывкам подписей на насыщенных листах.
    Он по-прежнему остаётся в ``rooms.findings`` для ручного просмотра.
    """

    return [
        Signal(
            source="room_registry",
            domain="room",
            key=finding.room_key,
            detail=finding.detail,
        )
        for finding in findings
        if getattr(finding, "finding_type", "") in _ROOM_SIGNAL_TYPES
    ]


def signals_from_equip_cross_check(findings) -> list[Signal]:
    """Сильные сигналы из реестра оборудования.

    ``missing_in_pd`` означает лишь, что в РД появилась дополнительная
    позиция. На реальных листах эта категория особенно шумная и сама по себе
    не должна создавать точку контроля. Исчезновение проектной позиции и
    изменение количества остаются сигналами.
    """

    return [
        Signal(
            source="equip_registry",
            domain="equipment",
            key=finding.equip_key,
            detail=finding.detail,
        )
        for finding in findings
        if getattr(finding, "finding_type", "") in _EQUIP_SIGNAL_TYPES
    ]


_ROUTING_FINDING_CATEGORIES = ("retargeted", "connection_count_changed")


def signals_from_routing_diff(diff: dict[str, list[dict]]) -> list[Signal]:
    """Сигналы только из категорий, которые означают изменение маршрута."""

    out: list[Signal] = []
    for category in _ROUTING_FINDING_CATEGORIES:
        for entry in diff.get(category, []):
            out.append(
                Signal(
                    source="routing",
                    domain="room",
                    key=entry["room_key"],
                    detail=category,
                )
            )
    return out


def signals_from_requirement_cross_check(findings) -> list[Signal]:
    """Сигналы из требований ПД, которые не удалось подтвердить текстом."""

    out: list[Signal] = []
    for finding in findings:
        if finding.finding_type == "no_code_visual_check_needed":
            for room in finding.rooms:
                out.append(
                    Signal(
                        source="requirement_prose",
                        domain="room",
                        key=room,
                        detail=finding.detail,
                    )
                )
        elif finding.finding_type == "code_missing_in_rd":
            out.append(
                Signal(
                    source="requirement_prose",
                    domain="requirement_code",
                    key=finding.code,
                    detail=finding.detail,
                )
            )
    return out


def signals_from_visual_requirement_checks(results: Sequence[dict]) -> list[Signal]:
    """Преобразовать проверку требований по листу РД в независимые vision-сигналы.

    Только ``absent`` является сигналом расхождения. ``confirmed`` означает,
    что требование на листе выполнено, а ``unclear`` — что доказательств
    недостаточно. Один результат может относиться к нескольким помещениям;
    тогда создаётся отдельный сигнал на каждый номер, чтобы он мог
    триангулироваться с ``requirement_prose``/``routing``/``room_registry``.
    """

    out: list[Signal] = []
    for result in results:
        if result.get("verdict") != "absent":
            continue
        reason = str(result.get("reason") or "").strip()
        where = str(result.get("where") or "").strip()
        detail = reason
        if where:
            detail = f"{detail} [{where}]" if detail else where
        for room in result.get("rooms") or []:
            room_key = str(room).strip()
            if room_key:
                out.append(
                    Signal(
                        source="vision",
                        domain="room",
                        key=room_key,
                        detail=detail,
                    )
                )
    return out


def signal_from_vision_verdict(room_key: str, detail: str = "") -> Signal:
    """Совместимый одиночный vision-сигнал."""

    return Signal(source="vision", domain="room", key=room_key, detail=detail)
