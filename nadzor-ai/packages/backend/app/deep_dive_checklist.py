"""Упорядоченный чек-лист фазы «вглубь» (Приложение Г.23 CLAUDE.md, приёмы
№4 и №5) — заменяет свободный визуальный обзор структурой, где пропуск
виден, а не только подразумевается.

Три текстовых напоминания подряд («сначала вширь, потом вглубь», «вглубь —
по убыванию скора», «большая пара — перечень, не свободный обзор») не
удержали фазу «вглубь» на паре с максимальным скором: приоритет по Г.19
внутри перечня помещений (приём №5) на практике подменял собой порядок
самих пар (приём №4) — пары с текстовым якорем разбирались раньше пары с
лучшим скором целиком, а не только раньше внутри неё самой (см. CLAUDE.md,
разбор восьмого прогона). Эта путаница возможна только в свободном тексте:
структура ниже физически разделяет два уровня порядка — пары сортируются
по скору один раз, до всякого учёта якорей; якорь Г.19 влияет только на
порядок помещений ВНУТРИ уже выбранной пары.

Третий уровень приоритета из Г.23 (помещения с индивидуально подписанной
веткой системы) здесь не реализован — для него нужен экстрактор «система ↔
помещение» (Приложение Г.15), которого в кодовой базе пока нет. Пока
реализовано два уровня: якорь Г.19 (высокий приоритет) и всё остальное.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .matching import PagePair


@dataclass
class RoomChecklistItem:
    room_key: str
    priority: str  # 'anchor' | 'plain' — см. докстринг модуля про третий уровень
    checked: bool = False
    finding: str | None = None  # None = ещё не разобрано


@dataclass
class DeepDiveEntry:
    pair: PagePair
    rooms: list[RoomChecklistItem] = field(default_factory=list)

    @property
    def progress(self) -> str:
        done = sum(1 for r in self.rooms if r.checked)
        return f"{done}/{len(self.rooms)}"


def build_deep_dive_checklist(
    drawing_pairs: list[PagePair],
    before_room_facts: list[dict],
    anchor_hits: set[str],
) -> list[DeepDiveEntry]:
    """`drawing_pairs` — уверенные чертёжные пары (`matched_by="text"`), в
    любом исходном порядке — сортировка по скору выполняется здесь, а не
    оставляется вызывающему коду, чтобы порядок пар нельзя было случайно
    подменить порядком построения списка.

    `before_room_facts` — `room_facts` стороны "до" (обычно ПД); учитываются
    записи с `page`, совпадающим с `pair.before_page`.

    `anchor_hits` — множество ключей помещений, уже подтверждённых
    Г.19 (`anchor_prose.find_anchor_in_prose`) — идут первыми внутри
    перечня своей пары, но не переставляют порядок самих пар."""
    ordered_pairs = sorted(drawing_pairs, key=lambda p: p.score, reverse=True)
    entries: list[DeepDiveEntry] = []
    for pair in ordered_pairs:
        room_keys = sorted(
            {f["key"] for f in before_room_facts
             if f["page"] == pair.before_page and f.get("key")},
            key=lambda k: (k not in anchor_hits, k),
        )
        items = [
            RoomChecklistItem(room_key=k, priority="anchor" if k in anchor_hits else "plain")
            for k in room_keys
        ]
        entries.append(DeepDiveEntry(pair=pair, rooms=items))
    return entries


def render_checklist_markdown(entries: list[DeepDiveEntry]) -> str:
    """Печатный вид чек-листа — то, что реально передаётся в работу
    аналитику/сессии: пары уже в порядке разбора, каждое помещение — своя
    строка с чекбоксом, прогресс виден по каждой паре отдельно."""
    lines = ["# Чек-лист фазы «вглубь» (пары строго по убыванию скора)"]
    for i, entry in enumerate(entries, start=1):
        p = entry.pair
        lines.append(
            f"\n## {i}. score={p.score:.3f} — ПД стр.{p.before_page} ↔ "
            f"РД стр.{p.after_page} (0/{len(entry.rooms)} помещений)"
        )
        if not entry.rooms:
            lines.append("_(реестр не дал номеров помещений для этого листа — "
                          "кроп листа целиком)_")
        for item in entry.rooms:
            mark = "x" if item.checked else " "
            tag = " (якорь Г.19)" if item.priority == "anchor" else ""
            lines.append(f"- [{mark}] {item.room_key}{tag}")
    return "\n".join(lines)
