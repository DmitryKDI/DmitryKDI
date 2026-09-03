"""Пакет эскалации (Г.30, п.5) — второе назначение статуса `candidate` из
`triangulation.py`: не просто «не подтверждено», а готовый закрытый вопрос
для человека со всем контекстом, который уже есть, вместо того чтобы
заново пересматривать оба тома.

Формат — по мотивам того, что реально закрыло вопрос по нарушению №2 при
прямой проверке этого комплекта (текст + схема + спецификация сошлись) и
что НЕ закрыло вопрос по нарушению №1 (раздвоенная позиция оборудования,
единственный источник — схема, спецификация не дала отдельных строк):
пакет обязан явно показать, какие источники СОГЛАСНЫ, каких НЕ ХВАТАЕТ, и
сформулировать вопрос так, чтобы ответ на него снял неоднозначность, а не
просто подтвердил то же самое ещё раз.

Ограничение этой версии (Г.10 — видимое, не молчаливое): пакет собирает
ТЕКСТОВЫЙ контекст (что уже извлечено — detail-строки сигналов), но не
рендерит кропы ПД/РД в картинку. `RoomFinding`/`EquipFinding` (см.
`room_cross_check.py`/`equip_cross_check.py`) не несут номер страницы —
без этого поля рендер кропа не построить. Следующий шаг, если понадобится:
завести `page` в эти датаклассы и рендерить кроп зрением здесь (по образцу
`stamp_vision.py`, единственного оставшегося после чистки Г.62 модуля
такого рода). Пока этого нет — пакет честно говорит «страница неизвестна»
вместо того, чтобы притворяться, что кропа не бывает."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .triangulation import CANDIDATE, Confirmation

# Источники, которые считаются «структурными» (число/код), а не текстовым
# суждением — расхождение только по ним само по себе безобидная
# перенумерация, если единственный источник, но обычно так и приходит от
# room_cross_check/equip_cross_check по конструкции (Г.9 уже отфильтровал
# явную перенумерацию на своём уровне, см. `_is_minor_variation`).
KNOWN_SOURCES = ("text", "prose", "schema", "vision", "balance",
                 "routing", "room_registry", "equip_registry")


@dataclass(frozen=True)
class EscalationTicket:
    """Готовый к передаче человеку пакет по одному кандидату."""
    domain: str
    key: str
    sources_present: tuple[str, ...]
    sources_missing: tuple[str, ...]
    context: tuple[str, ...]
    question: str


def _missing_sources(present: Sequence[str], known: Sequence[str] = KNOWN_SOURCES) -> tuple[str, ...]:
    return tuple(s for s in known if s not in present)


def build_ticket(confirmation: Confirmation, known_sources: Sequence[str] = KNOWN_SOURCES) -> EscalationTicket:
    """Собирает пакет эскалации из одного `Confirmation` со статусом
    `candidate`. Для `confirmed` тоже отработает технически, но смысла нет —
    вопрос уже закрыт минимум двумя источниками."""
    present = confirmation.sources
    missing = _missing_sources(present, known_sources)
    label = "помещение" if confirmation.domain == "room" else "позиция оборудования"

    if not missing:
        # Все известные источники уже отметились одним и тем же ключом, но
        # триангуляция всё равно не набрала порог (например, min_sources
        # выставлен выше, чем число вообще существующих источников) —
        # честно об этом сказать, а не выдумывать, чего не хватает.
        question = (f"{label} {confirmation.key}: отметились источники "
                    f"{', '.join(present)}, но этого недостаточно для "
                    f"подтверждения по текущему порогу — увеличить число "
                    f"источников больше нечем, решение за человеком")
    else:
        question = (f"{label} {confirmation.key}: расхождение подтвердил только "
                    f"{'/'.join(present)}. Проверить дополнительно через "
                    f"{'/'.join(missing)} — если хотя бы один подтвердит то же "
                    f"самое, находка переходит в confirmed; если ни один не "
                    f"применим (например, для этого места просто нет схемы или "
                    f"спецификации) — решить, достаточно ли одного источника "
                    f"в этом конкретном случае")

    return EscalationTicket(
        domain=confirmation.domain, key=confirmation.key,
        sources_present=present, sources_missing=missing,
        context=confirmation.details, question=question)


def build_tickets(confirmations: Sequence[Confirmation],
                  known_sources: Sequence[str] = KNOWN_SOURCES) -> list[EscalationTicket]:
    """Пакеты для всех `candidate`-находок сразу; `confirmed` пропускаются —
    им пакет эскалации не нужен, вопрос уже закрыт."""
    return [build_ticket(c, known_sources) for c in confirmations if c.status == CANDIDATE]


def render_ticket_markdown(ticket: EscalationTicket) -> str:
    label = "Помещение" if ticket.domain == "room" else "Позиция оборудования"
    lines = [
        f"### {label} {ticket.key}",
        f"- Подтвердили: {', '.join(ticket.sources_present) or '—'}",
        f"- Не проверено: {', '.join(ticket.sources_missing) or '—'}",
    ]
    if ticket.context:
        lines.append("- Контекст:")
        for c in ticket.context:
            lines.append(f"  - {c}")
    lines.append(f"- Вопрос: {ticket.question}")
    return "\n".join(lines)


def render_tickets_markdown(tickets: Sequence[EscalationTicket]) -> str:
    if not tickets:
        return "Очередь эскалации пуста — все находки подтверждены минимум двумя источниками."
    header = f"## Очередь эскалации ({len(tickets)})\n"
    return header + "\n\n".join(render_ticket_markdown(t) for t in tickets)
