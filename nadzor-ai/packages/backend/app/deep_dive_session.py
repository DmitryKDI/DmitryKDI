"""Пошаговая сессия фазы «вглубь» (Приложение Г.23) — отдаёт по одному
пункту за раз, а не печатает список целиком.

Девятый прогон: `deep_dive_checklist.render_checklist_markdown` даёт верный
порядок (пары по убыванию скора), но это статичный документ — его можно
прочитать целиком и всё равно пойти по интересу, что и произошло: сессия
сама признала «механизм отдал правильный порядок — я его не соблюл», уйдя
на пары с якорем Г.19 раньше пары с максимальным скором. Показ всего
списка вперёд — источник соблазна: видно, что впереди «интереснее».

`DeepDiveSession` устраняет именно list видимость: `current()` возвращает
ТОЛЬКО текущий пункт (пара + помещение), без списка последующих. Перейти
дальше можно единственным способом — вызвать `resolve()` для текущего
пункта. Честно: это не абсолютный барьер (ничто технически не мешает
работающей сессии просто не пользоваться этим объектом и вырезать кроп
руками), но убирает сам список-искушение — открытый вопрос, снимает ли
этого достаточно, или нужен реальный автоматический проход (тогда это уже
не подсказка аналитику, а сам механизм Г.7)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .deep_dive_checklist import (
    DEPTH_TEXT,
    DEPTH_VISUAL,
    DeepDiveEntry,
    build_deep_dive_checklist,
)
from .matching import PagePair


@dataclass
class CurrentItem:
    pair_index: int  # 1-based, для человекочитаемого вывода
    pair_total: int
    before_page: int
    after_page: int
    score: float
    room_key: Optional[str]
    room_priority: Optional[str]
    room_index: int  # 1-based; 0, если у пары вообще нет помещений в реестре
    room_total: int
    note: Optional[str] = None


class DeepDiveSession:
    def __init__(self, entries: list[DeepDiveEntry]):
        self._entries = entries
        self._pair_idx = 0
        self._room_idx = 0
        self._skip_advance()

    @classmethod
    def start(
        cls,
        drawing_pairs: list[PagePair],
        before_room_facts: list[dict],
        anchor_hits: set[str],
    ) -> "DeepDiveSession":
        return cls(build_deep_dive_checklist(drawing_pairs, before_room_facts, anchor_hits))

    def current(self) -> Optional[CurrentItem]:
        """Текущий пункт или None, если чек-лист пройден целиком. Никогда
        не сообщает, что идёт дальше — намеренно."""
        if self._pair_idx >= len(self._entries):
            return None
        entry = self._entries[self._pair_idx]
        if not entry.rooms:
            return CurrentItem(
                pair_index=self._pair_idx + 1, pair_total=len(self._entries),
                before_page=entry.pair.before_page, after_page=entry.pair.after_page,
                score=entry.pair.score, room_key=None, room_priority=None,
                room_index=0, room_total=0,
                note="реестр не дал номеров помещений — кроп листа целиком",
            )
        room = entry.rooms[self._room_idx]
        return CurrentItem(
            pair_index=self._pair_idx + 1, pair_total=len(self._entries),
            before_page=entry.pair.before_page, after_page=entry.pair.after_page,
            score=entry.pair.score, room_key=room.room_key, room_priority=room.priority,
            room_index=self._room_idx + 1, room_total=len(entry.rooms),
        )

    def resolve(self, depth: str, finding: Optional[str] = None) -> None:
        """Закрывает текущий пункт и продвигает сессию — единственный
        способ дойти до следующего пункта.

        `depth` обязателен и говорит, КАК пункт закрыт: `DEPTH_TEXT`
        (сверил название/реестр текстом) или `DEPTH_VISUAL` (вырезал зону и
        посмотрел графику). Десятый прогон закрыл все 531 пункт, но почти
        все — текстом, при том что реальные находки требовали кропа; одно
        булево «проверено» это скрывало (Г.25). Параметр без значения по
        умолчанию именно поэтому: способ должен называться явно, а не
        подставляться молча."""
        if depth not in (DEPTH_TEXT, DEPTH_VISUAL):
            raise ValueError(
                f"depth должен быть {DEPTH_TEXT!r} или {DEPTH_VISUAL!r}, получено {depth!r}")
        if self._pair_idx >= len(self._entries):
            return
        entry = self._entries[self._pair_idx]
        if entry.rooms:
            entry.rooms[self._room_idx].checked = True
            entry.rooms[self._room_idx].finding = finding
            entry.rooms[self._room_idx].depth = depth
            self._room_idx += 1
        else:
            self._room_idx = 1
        self._skip_advance()

    def skip_rest_of_pair(self, reason: str) -> None:
        """Честно обрезать хвост ТЕКУЩЕЙ пары (например, бюджет времени) —
        но не перепрыгнуть вперёд произвольно: следующая пара всё равно
        следующая по скору, не любая, которая покажется интереснее."""
        if self._pair_idx >= len(self._entries):
            return
        entry = self._entries[self._pair_idx]
        for room in entry.rooms[self._room_idx:]:
            room.finding = f"[пропущено] {reason}"
        self._pair_idx += 1
        self._room_idx = 0

    def status(self) -> str:
        """Сводка прогресса по всем парам — для отчёта в конце, не для
        навигации по ходу работы (`current()` — единственный источник
        того, что делать дальше). Кроп показан отдельно от общего числа
        закрытых пунктов: 531/531 при 4 кропах и 531/531 при 200 кропах —
        это принципиально разные прогоны, и отчёт обязан их различать
        (Г.25)."""
        lines = []
        total_visual = 0
        total_checked = 0
        for i, entry in enumerate(self._entries):
            marker = "->" if i == self._pair_idx else "  "
            lines.append(f"{marker} {i + 1}. score={entry.pair.score:.3f} {entry.progress}")
            total_checked += sum(1 for r in entry.rooms if r.checked)
            total_visual += sum(1 for r in entry.rooms if r.depth == DEPTH_VISUAL)
        lines.append(
            f"ИТОГО: закрыто {total_checked}, из них кропом {total_visual}, "
            f"текстом {total_checked - total_visual}")
        return "\n".join(lines)

    def _skip_advance(self) -> None:
        while self._pair_idx < len(self._entries):
            entry = self._entries[self._pair_idx]
            # Пара без помещений в реестре — один псевдо-пункт «лист
            # целиком» (total=1), а не бесконечно «пусто, пропускаем».
            total = len(entry.rooms) if entry.rooms else 1
            if self._room_idx < total:
                return
            self._pair_idx += 1
            self._room_idx = 0
