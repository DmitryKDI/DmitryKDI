import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import level_pages, routing_diff
from app.documents import DocumentFacts
from app.routing_diff import (
    build_edges_for_rooms,
    candidate_plan_pages,
    diff_room_routing,
    render_routing_diff_report,
)
from app.routing_graph import RoutingEdge, RoutingGraph


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_candidate_plan_pages_prefers_drawing_pages_with_room_facts():
    facts = DocumentFacts(
        name="rd.pdf", pages=5,
        text_facts=[],
        room_facts=[
            {"page": 3, "key": "140", "name": "Коридор"},  # экспликация
            {"page": 7, "key": "140", "name": "Коридор"},  # план
        ],
        page_kinds={3: "text", 7: "drawing"},
    )
    orig = _patch(routing_diff, "extract_document_facts", lambda path, name: facts)
    try:
        candidates = candidate_plan_pages(["rd.pdf"], ["140"])
    finally:
        routing_diff.extract_document_facts = orig

    assert candidates["140"] == [{"path": "rd.pdf", "page": 7}], "экспликация не должна попасть в кандидаты плана"


def test_candidate_plan_pages_falls_back_to_level_when_no_drawing_page_has_the_room():
    facts = DocumentFacts(
        name="rd.pdf", pages=2,
        text_facts=[
            {"page": 3, "text": "Экспликация 2 этаж +1.750"},
            {"page": 9, "text": "План 2 этажа +1.750"},
        ],
        room_facts=[{"page": 3, "key": "142", "name": "Санузел"}],
        page_kinds={3: "text", 9: "drawing"},
    )
    orig = _patch(routing_diff, "extract_document_facts", lambda path, name: facts)
    orig_level = _patch(level_pages, "extract_document_facts", lambda path, name: facts)
    try:
        candidates = candidate_plan_pages(["rd.pdf"], ["142"])
    finally:
        routing_diff.extract_document_facts = orig
        level_pages.extract_document_facts = orig_level

    assert candidates["142"] == [{"path": "rd.pdf", "page": 9}]


def test_build_edges_for_rooms_uses_first_page_with_a_resolved_edge():
    facts = DocumentFacts(
        name="rd.pdf", pages=2,
        text_facts=[], room_facts=[{"page": 1, "key": "140", "name": "х"}, {"page": 2, "key": "140", "name": "х"}],
        page_kinds={1: "drawing", 2: "drawing"},
    )
    unresolved_graph = RoutingGraph(edges=(RoutingEdge(branch_code="В1", room_key="140", resolved=False, reason="не прослежено"),))
    resolved_graph = RoutingGraph(edges=(RoutingEdge(branch_code="В2", room_key="140", target_code="поз.1", resolved=True),))

    calls = []

    def fake_build_routing_graph(page, room_keys=None, **kw):
        calls.append(page)
        return unresolved_graph if len(calls) == 1 else resolved_graph

    class _FakeDoc:
        def __getitem__(self, i):
            return f"page-{i}"

        def close(self):
            pass

    orig_facts = _patch(routing_diff, "extract_document_facts", lambda path, name: facts)
    orig_open = _patch(routing_diff, "open_pdf", lambda path: _FakeDoc())
    orig_build = _patch(routing_diff, "build_routing_graph", fake_build_routing_graph)
    try:
        edges = build_edges_for_rooms(["rd.pdf"], ["140"])
    finally:
        routing_diff.extract_document_facts = orig_facts
        routing_diff.open_pdf = orig_open
        routing_diff.build_routing_graph = orig_build

    assert len(calls) == 2, "первая (нерешённая) страница не должна остановить перебор"
    assert len(edges) == 1
    assert edges[0].resolved and edges[0].target_code == "поз.1"


def test_build_edges_for_rooms_reports_unresolved_room_visibly():
    orig = _patch(routing_diff, "extract_document_facts", lambda path, name: DocumentFacts(name=name, pages=0, text_facts=[], room_facts=[]))
    try:
        edges = build_edges_for_rooms(["rd.pdf"], ["999"])
    finally:
        routing_diff.extract_document_facts = orig

    assert len(edges) == 1
    assert edges[0].resolved is False
    assert "не найдено" in edges[0].reason
    print("OK:", edges[0].reason)


def test_diff_room_routing_detects_retargeted_room():
    before_edges = [RoutingEdge(branch_code="В2.1", room_key="140", target_code="поз.100", resolved=True)]
    after_edges = [RoutingEdge(branch_code="В2.1", room_key="140", target_code="поз.200", resolved=True)]
    calls = {"n": 0}

    def fake_build_edges_for_rooms(paths, room_keys, max_pages_per_room=2):
        calls["n"] += 1
        return before_edges if calls["n"] == 1 else after_edges

    orig = _patch(routing_diff, "build_edges_for_rooms", fake_build_edges_for_rooms)
    try:
        diff = diff_room_routing(["pd.pdf"], ["rd.pdf"], ["140"])
    finally:
        routing_diff.build_edges_for_rooms = orig

    assert len(diff["retargeted"]) == 1
    assert diff["retargeted"][0]["room_key"] == "140"

    report = render_routing_diff_report(diff)
    assert "140" in report and "точке сбора" in report


if __name__ == "__main__":
    test_candidate_plan_pages_prefers_drawing_pages_with_room_facts()
    test_candidate_plan_pages_falls_back_to_level_when_no_drawing_page_has_the_room()
    test_build_edges_for_rooms_uses_first_page_with_a_resolved_edge()
    test_build_edges_for_rooms_reports_unresolved_room_visibly()
    test_diff_room_routing_detects_retargeted_room()
    print("ALL PASS")
