from __future__ import annotations

import app.semantic_pair_runtime as runtime
from app.control_pair_candidates import ControlPair
from app.matching import DocumentInput


def _doc(name: str) -> DocumentInput:
    return DocumentInput(name=name, pages=1, page_kinds={1: "drawing"})


def _grounded_difference(label: str) -> dict:
    return {
        "pd_claim": f"ПД показывает {label} A",
        "pd_evidence": f"на ПД непосредственно виден {label} A",
        "rd_claim": f"РД показывает {label} B",
        "rd_evidence": f"на РД непосредственно виден {label} B",
        "difference": f"{label}: A -> B",
        "where": "зона 1",
    }


def test_every_selected_pair_gets_whole_page_call_without_anchors(monkeypatch):
    pairs = [
        ControlPair(0, 1, 0, 1, 0.05, "weak", (), ()),
        ControlPair(0, 1, 1, 1, 0.04, "weak", (), ()),
    ]
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: pairs)
    calls = []

    def fake_call(state, config, region=None):
        calls.append((state.after_path, region))
        return {
            "comparability": "high",
            "findings": [],
            "candidate_regions": [],
            "uncertainties": [],
        }

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    signals, diagnostics = runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("a"), _doc("b")], ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2
    )

    assert signals == []
    assert calls == [("a.pdf", None), ("b.pdf", None)]
    rows = [x for x in diagnostics if x.get("control_type") == "page_pair"]
    assert len(rows) == 2
    assert all(row["whole_page_done"] for row in rows)


def test_bare_change_without_two_sided_evidence_is_not_a_finding(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "candidate_pairs",
        lambda *args: [ControlPair(0, 1, 0, 1, 0.9, "text", (), ())],
    )
    monkeypatch.setattr(
        runtime,
        "_call_model",
        lambda *args, **kwargs: {
            "comparability": "high",
            "findings": [{"difference": "что-то изменено"}],
            "candidate_regions": [],
            "uncertainties": [],
        },
    )

    signals, diagnostics = runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1
    )

    assert signals == []
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "compared_no_candidate"
    assert row["confirmed_findings"] == []


def test_medium_whole_page_candidate_requires_zoom_before_confirmation(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "candidate_pairs",
        lambda *args: [ControlPair(0, 1, 0, 1, 0.2, "text", (), ())],
    )
    order = []

    def fake_call(state, config, region=None):
        order.append(region)
        if region is None:
            return {
                "comparability": "medium",
                "findings": [_grounded_difference("ветка")],
                "candidate_regions": [{
                    "reason": "увеличить узел",
                    "pd_bbox_norm": [0.1, 0.1, 0.4, 0.4],
                    "rd_bbox_norm": [0.2, 0.2, 0.5, 0.5],
                    "priority": "high",
                }],
                "uncertainties": ["мелкий масштаб"],
            }
        return {
            "comparability": "high",
            "findings": [_grounded_difference("ветка")],
            "candidate_regions": [],
            "uncertainties": [],
        }

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    signals, diagnostics = runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1
    )

    assert len(order) == 2
    assert order[0] is None and order[1] is not None
    assert len([s for s in signals if s.source == "vision_pair"]) == 1
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "confirmed_difference"
    assert row["candidate_regions_checked"] == 1


def test_zoom_scheduling_is_round_robin(monkeypatch):
    pairs = [
        ControlPair(0, 1, 0, 1, 0.2, "text", (), ()),
        ControlPair(0, 1, 1, 1, 0.2, "text", (), ()),
    ]
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: pairs)
    order = []

    def fake_call(state, config, region=None):
        order.append((state.after_path, region is not None))
        if region is None:
            return {
                "comparability": "medium",
                "findings": [],
                "candidate_regions": [{
                    "reason": "zoom",
                    "pd_bbox_norm": [0.1, 0.1, 0.3, 0.3],
                    "rd_bbox_norm": [0.1, 0.1, 0.3, 0.3],
                    "priority": "high",
                }],
                "uncertainties": [],
            }
        return {"comparability": "high", "findings": [], "candidate_regions": [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("a"), _doc("b")], ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2
    )

    assert order[:2] == [("a.pdf", False), ("b.pdf", False)]
    assert order[2:] == [("a.pdf", True), ("b.pdf", True)]


def test_low_comparability_never_becomes_no_difference(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "candidate_pairs",
        lambda *args: [ControlPair(0, 1, 0, 1, 0.8, "text", (), ())],
    )
    monkeypatch.setattr(
        runtime,
        "_call_model",
        lambda *args, **kwargs: {
            "comparability": "low",
            "findings": [],
            "candidate_regions": [],
            "uncertainties": ["не читается"],
        },
    )

    _, diagnostics = runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1
    )
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "unclear"


def test_coverage_reports_legacy_orchestrators_inactive(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [])
    _, diagnostics = runtime.run_targeted_pair_vision(
        [_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1
    )
    coverage = diagnostics[-1]
    assert coverage["legacy_room_runtime_active"] is False
    assert coverage["legacy_generic_region_runtime_active"] is False
    assert coverage["raster_gate_active"] is False
