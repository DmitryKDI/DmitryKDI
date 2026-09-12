from __future__ import annotations

import app.simple_pair_runtime as simple
from app.control_pair_candidates import ControlPair
from app.matching import DocumentInput
from app.triangulation import CONFIRMED, Signal, triangulate


def _doc(name: str) -> DocumentInput:
    return DocumentInput(name=name, pages=1, page_kinds={1: "drawing"})


def test_all_selected_pairs_get_whole_page_semantic_call_even_without_anchors(monkeypatch):
    pairs = [
        ControlPair(0, 1, 0, 1, 0.05, "weak", (), ()),
        ControlPair(0, 1, 1, 1, 0.04, "weak", (), ()),
    ]
    monkeypatch.setattr(simple, "candidate_pairs", lambda *args: pairs)
    monkeypatch.setattr(simple, "visual_change_evidence", lambda *args: {"significant": False})
    calls = []

    def compare(*args, **kwargs):
        calls.append((args[1], args[3], kwargs.get("clip")))
        return {
            "comparability": "high",
            "differences": [],
            "candidate_regions": [],
            "unclear_reason": "",
        }

    monkeypatch.setattr(simple, "semantic_scope_compare", compare)
    before = [_doc("pd")]
    after = [_doc("rd-a"), _doc("rd-b")]

    signals, diagnostics = simple.run_targeted_pair_vision(
        before, after, ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2
    )

    assert signals == []
    assert len(calls) == 2
    assert all(call[2] is None for call in calls)
    pair_rows = [row for row in diagnostics if row.get("control_type") == "page_pair"]
    assert len(pair_rows) == 2
    assert all(row["whole_page_done"] is True for row in pair_rows)


def test_model_regions_are_zoomed_round_robin_after_whole_page(monkeypatch):
    pairs = [
        ControlPair(0, 1, 0, 1, 0.2, "text", (), ()),
        ControlPair(0, 1, 1, 1, 0.2, "text", (), ()),
    ]
    monkeypatch.setattr(simple, "candidate_pairs", lambda *args: pairs)
    monkeypatch.setattr(simple, "visual_change_evidence", lambda *args: {"significant": False})
    order = []

    def compare(before_path, before_page, after_path, after_page, *args, **kwargs):
        clip = kwargs.get("clip")
        order.append((after_path, clip))
        if clip is None:
            return {
                "comparability": "medium",
                "differences": [],
                "candidate_regions": [
                    {"rd_bbox_norm": [0.1, 0.1, 0.4, 0.4], "priority": "high"}
                ],
                "unclear_reason": "need zoom",
            }
        return {
            "comparability": "high",
            "differences": [{"change": f"difference on {after_path}"}],
            "candidate_regions": [],
            "unclear_reason": "",
        }

    monkeypatch.setattr(simple, "semantic_scope_compare", compare)
    signals, _ = simple.run_targeted_pair_vision(
        [_doc("pd")], [_doc("a"), _doc("b")], ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2
    )

    assert order[0] == ("a.pdf", None)
    assert order[1] == ("b.pdf", None)
    assert order[2][0] == "a.pdf" and order[2][1] is not None
    assert order[3][0] == "b.pdf" and order[3][1] is not None
    assert len([signal for signal in signals if signal.source == "vision_pair"]) == 2


def test_semantic_vision_is_not_blocked_by_two_source_triangulation_rule():
    result = triangulate([Signal("vision_pair", "page_pair", "0:1->0:1", "changed")])
    assert len(result) == 1
    assert result[0].status == CONFIRMED


def test_registry_only_signal_still_needs_corroboration():
    result = triangulate([Signal("room_registry", "room", "101", "changed")])
    assert len(result) == 1
    assert result[0].status != CONFIRMED
