import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.escalation import build_ticket, build_tickets, render_ticket_markdown, render_tickets_markdown
from app.triangulation import CANDIDATE, CONFIRMED, Confirmation


def test_ticket_lists_present_and_missing_sources():
    confirmation = Confirmation(domain="room", key="012", status=CANDIDATE,
                                sources=("schema",), details=("раздвоение позиции П17",))
    ticket = build_ticket(confirmation)
    assert ticket.sources_present == ("schema",), ticket
    assert "text" in ticket.sources_missing, ticket
    assert "schema" not in ticket.sources_missing, ticket
    print("OK: пакет эскалации разделяет подтвердившие и непроверенные источники")


def test_ticket_carries_context_from_signals():
    confirmation = Confirmation(domain="room", key="140", status=CANDIDATE,
                                sources=("prose",), details=("в помещении 140 заменена система",))
    ticket = build_ticket(confirmation)
    assert ticket.context == ("в помещении 140 заменена система",), ticket
    print("OK: контекст сигналов переносится в пакет эскалации без потерь")


def test_ticket_when_all_known_sources_already_present():
    """Реальный краевой случай: все источники, которые вообще существуют
    для этого домена, уже отметились одним и тем же ключом, но порог
    триангуляции всё равно не пройден (например min_sources выставлен
    искусственно высоко) — пакет не должен выдумывать несуществующий
    «недостающий» источник."""
    from app.escalation import KNOWN_SOURCES
    confirmation = Confirmation(domain="room", key="267", status=CANDIDATE,
                                sources=tuple(KNOWN_SOURCES))
    ticket = build_ticket(confirmation)
    assert ticket.sources_missing == (), ticket
    assert "решение за человеком" in ticket.question, ticket
    print("OK: когда проверять больше нечем, пакет честно говорит об этом, не выдумывает")


def test_build_tickets_skips_confirmed():
    confirmations = [
        Confirmation(domain="room", key="1", status=CONFIRMED, sources=("a", "b")),
        Confirmation(domain="room", key="2", status=CANDIDATE, sources=("a",)),
    ]
    tickets = build_tickets(confirmations)
    assert len(tickets) == 1, tickets
    assert tickets[0].key == "2", tickets
    print("OK: подтверждённые находки не попадают в очередь эскалации")


def test_render_ticket_markdown_contains_key_fields():
    confirmation = Confirmation(domain="equipment", key="14", status=CANDIDATE,
                                sources=("schema",), details=("П17.1/17.2",))
    ticket = build_ticket(confirmation)
    md = render_ticket_markdown(ticket)
    assert "14" in md
    assert "schema" in md
    assert "П17.1/17.2" in md
    print("OK: markdown-рендер одного пакета содержит ключ, источники и контекст")


def test_render_tickets_markdown_empty_queue():
    assert "пуста" in render_tickets_markdown([])
    print("OK: пустая очередь эскалации явно сообщает, что всё подтверждено")


def test_render_tickets_markdown_multiple():
    confirmations = [
        Confirmation(domain="room", key="147", status=CANDIDATE, sources=("routing",)),
        Confirmation(domain="room", key="198", status=CANDIDATE, sources=("routing",)),
    ]
    md = render_tickets_markdown(build_tickets(confirmations))
    assert "147" in md and "198" in md
    assert md.startswith("## Очередь эскалации (2)")
    print("OK: рендер нескольких пакетов сохраняет оба ключа и общий счётчик")


if __name__ == "__main__":
    test_ticket_lists_present_and_missing_sources()
    test_ticket_carries_context_from_signals()
    test_ticket_when_all_known_sources_already_present()
    test_build_tickets_skips_confirmed()
    test_render_ticket_markdown_contains_key_fields()
    test_render_tickets_markdown_empty_queue()
    test_render_tickets_markdown_multiple()
    print("ALL PASS")
