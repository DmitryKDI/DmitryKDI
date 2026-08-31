"""Правило триангуляции источников (Г.30, п.4).

Разные модули этого пакета независимо производят сигналы о возможном
расхождении по одному и тому же помещению/позиции: реестр помещений
(`room_cross_check.py`), реестр оборудования (`equip_cross_check.py`), якорь
в прозе пояснительной записки (`anchor_prose.py`), граф маршрутизации
(`routing_graph.py`), и — вне этого пакета, из свободного LLM-вызова —
сравнение схем зрением. У нарушения №2 из прямой проверки этого комплекта
(тёплые полы) все три источника — текст, схема, спецификация — независимо
подтвердили одно и то же; у нарушения №1 (раздвоенная позиция оборудования)
источник был ровно один, и вопрос остался неразрешённым. Это наблюдение,
не гипотеза, и его стоит формализовать как правило, а не полагаться на то,
что кто-то заметит совпадение вручную.

Правило: находка получает статус `confirmed`, только если минимум
`min_sources` (по умолчанию 2) РАЗНЫХ источников независимо указали на один
и тот же ключ (помещение/позицию). Меньше — `candidate`: годится для очереди
эскалации (Г.30 п.5), не для готового отчёта инспектору.

Модуль не решает, что считать «сигналом» — это работа отдельных
`signals_from_*`-адаптеров, каждый со своим модулем-источником. Сама
триангуляция — чистая агрегация, без обращения к LLM/vision и без знания
внутреннего устройства источников."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

CONFIRMED = "confirmed"
CANDIDATE = "candidate"

DEFAULT_MIN_SOURCES = 2


@dataclass(frozen=True)
class Signal:
    """Один независимый сигнал о возможном расхождении по ключу `key` в
    области `domain` («room» | «equipment» — разные пространства ключей,
    номер помещения и код позиции оборудования могут случайно совпасть
    строкой). `source` — имя модуля/канала, породившего сигнал; несколько
    сигналов с ОДНИМ И ТЕМ ЖЕ `source` по одному ключу считаются одним
    источником, не удваивают уверенность."""
    source: str
    domain: str
    key: str
    detail: str = ""


@dataclass(frozen=True)
class Confirmation:
    """Итог триангуляции по одному (domain, key)."""
    domain: str
    key: str
    status: str  # confirmed | candidate
    sources: tuple[str, ...] = ()
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def source_count(self) -> int:
        return len(self.sources)


def triangulate(signals: Sequence[Signal], min_sources: int = DEFAULT_MIN_SOURCES) -> list[Confirmation]:
    """Группирует сигналы по (domain, key) и присваивает статус по числу
    РАЗЛИЧНЫХ источников. Порядок результата — по ключу, для воспроизводимого
    отчёта."""
    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)
    for s in signals:
        grouped[(s.domain, s.key)].append(s)

    out: list[Confirmation] = []
    for (domain, key), group in grouped.items():
        sources = tuple(sorted({s.source for s in group}))
        details = tuple(s.detail for s in group if s.detail)
        status = CONFIRMED if len(sources) >= min_sources else CANDIDATE
        out.append(Confirmation(domain=domain, key=key, status=status,
                                sources=sources, details=details))
    out.sort(key=lambda c: (c.domain, c.key))
    return out


def confirmed_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [c for c in confirmations if c.status == CONFIRMED]


def candidates_only(confirmations: Sequence[Confirmation]) -> list[Confirmation]:
    return [c for c in confirmations if c.status == CANDIDATE]


# --------------------------------------------------------------------------
# Адаптеры: превращают вывод конкретного модуля-источника в список Signal.
# Каждый — тонкая обёртка, без собственной логики принятия решений.
# --------------------------------------------------------------------------

def signals_from_room_cross_check(findings) -> list[Signal]:
    """`findings` — `RoomCrossCheckResult.findings` (room_cross_check.py)."""
    return [Signal(source="room_registry", domain="room", key=f.room_key, detail=f.detail)
            for f in findings]


def signals_from_equip_cross_check(findings) -> list[Signal]:
    """`findings` — `EquipCrossCheckResult.findings` (equip_cross_check.py)."""
    return [Signal(source="equip_registry", domain="equipment", key=f.equip_key, detail=f.detail)
            for f in findings]


def signals_from_anchor_prose(hits: list[dict]) -> list[Signal]:
    """`hits` — результат `find_anchor_in_prose` (anchor_prose.py):
    [{page, anchor, paragraph}]. `anchor` здесь предполагается номером
    помещения — вызывающий код сам решает, какие якоря он туда передавал
    (anchor_prose.py одинаково ищет и номера помещений, и обозначения
    систем; для последних используйте domain вручную через Signal напрямую,
    не через этот адаптер)."""
    return [Signal(source="prose", domain="room", key=h["anchor"],
                   detail=h["paragraph"][:200]) for h in hits]


_ROUTING_FINDING_CATEGORIES = ("retargeted", "connection_count_changed")


def signals_from_routing_diff(diff: dict[str, list[dict]]) -> list[Signal]:
    """`diff` — результат `diff_routing_graphs` (routing_graph.py). Только
    категории, которые сама функция считает находками (`retargeted`,
    `connection_count_changed`) — `renumbered`/`unchanged` не сигнал, а
    `unusable`/`room_only_*` — другой вопрос (неразрешённость, комплектность),
    не про маршрутизацию, поэтому тоже не сигнал здесь."""
    out: list[Signal] = []
    for category in _ROUTING_FINDING_CATEGORIES:
        for entry in diff.get(category, []):
            out.append(Signal(source="routing", domain="room", key=entry["room_key"],
                              detail=f"{category}"))
    return out


def signals_from_requirement_cross_check(findings) -> list[Signal]:
    """`findings` — `RequirementCrossCheckResult.findings`
    (requirement_cross_check.py). Только `*_missing_in_rd` — расхождение;
    `*_confirmed_in_rd` записи в том же списке означают совпадение, а не
    сигнал о возможном расхождении, и намеренно сюда не попадают (тот же
    принцип, что и в остальных `signals_from_*`: сигнал — это находка о
    несовпадении, не запись о подтверждённом соответствии).

    Требование без кода (`predicate_missing_in_rd`) раскладывается на
    отдельный сигнал по КАЖДОМУ помещению из его списка (domain="room")
    — так это естественно складывается в триангуляции с сигналами
    `room_registry`/`prose` по тому же номеру, что и произошло вручную с
    нарушением №2 в этой сессии. Требование с кодом
    (`code_missing_in_rd`) не привязано к одному номеру помещения так же
    однозначно — сигнал по коду идёт в отдельный домен
    `requirement_code`, не смешиваясь с доменом `room`."""
    out: list[Signal] = []
    for f in findings:
        if f.finding_type == "predicate_missing_in_rd":
            for room in f.rooms:
                out.append(Signal(source="requirement_prose", domain="room", key=room, detail=f.detail))
        elif f.finding_type == "code_missing_in_rd":
            out.append(Signal(source="requirement_prose", domain="requirement_code", key=f.code, detail=f.detail))
    return out


def signal_from_vision_verdict(room_key: str, detail: str = "") -> Signal:
    """Для сигнала из LLM-сравнения схем (`vision.compare_page_pair` и
    аналоги) — единственный источник без готового адаптера, потому что сам
    вердикт модели не структурирован по номеру помещения жёстко (свободный
    JSON от провайдера). Вызывающий код сам решает, какой room_key вывод
    модели описывает, и явно строит Signal — здесь только для единообразия
    имени источника («vision») между разными вызовами."""
    return Signal(source="vision", domain="room", key=room_key, detail=detail)
