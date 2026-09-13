from __future__ import annotations

import app.semantic_pair_runtime as runtime
from app.control_pair_candidates import ControlPair
from app.matching import DocumentInput


def _doc(name: str) -> DocumentInput:
    return DocumentInput(name=name, pages=1, page_kinds={1: "drawing"})


def _inventory(label: str) -> list[dict]:
    return [{"entity": label, "observation": f"виден {label}", "where": "зона"}]


def _grounded_difference(label: str) -> dict:
    return {
        "pd_claim": f"ПД показывает {label} A",
        "pd_evidence": f"на ПД непосредственно виден {label} A",
        "rd_claim": f"РД показывает {label} B",
        "rd_evidence": f"на РД непосредственно виден {label} B",
        "difference": f"{label}: A -> B",
        "where": "зона 1",
    }


def _regions(count: int = 2) -> list[dict]:
    return [
        {
            "reason": f"zoom {i}",
            "pd_bbox_norm": [0.1 * i, 0.1, min(0.1 * i + 0.2, 0.95), 0.4],
            "rd_bbox_norm": [0.1 * i, 0.1, min(0.1 * i + 0.2, 0.95), 0.4],
            "priority": "high" if i == 1 else "medium",
        }
        for i in range(1, count + 1)
    ]


def test_every_selected_pair_gets_whole_page_call_without_anchors(monkeypatch):
    pairs = [ControlPair(0, 1, 0, 1, 0.05, "weak", (), ()), ControlPair(0, 1, 1, 1, 0.04, "weak", (), ())]
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: pairs)
    calls = []

    def fake_call(state, config, region=None, *, discover_regions=False):
        calls.append((state.after_path, region, discover_regions))
        return {"comparability": "high", "pd_inventory": _inventory("ПД"), "rd_inventory": _inventory("РД"), "findings": [], "candidate_regions": _regions(2), "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    runtime.run_targeted_pair_vision([_doc("pd")], [_doc("a"), _doc("b")], ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2)
    assert calls[0] == ("a.pdf", None, False)
    assert calls[1] == ("b.pdf", None, False)


def test_clean_whole_page_without_regions_triggers_region_discovery(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [ControlPair(0, 1, 0, 1, 0.9, "text", (), ())])
    calls = []

    def fake_call(state, config, region=None, *, discover_regions=False):
        calls.append((region, discover_regions))
        if discover_regions:
            return {"comparability": "high", "candidate_regions": _regions(2), "uncertainties": []}
        if region is None:
            return {"comparability": "high", "pd_inventory": _inventory("ПД"), "rd_inventory": _inventory("РД"), "findings": [], "candidate_regions": [], "uncertainties": []}
        return {"comparability": "high", "findings": [], "candidate_regions": [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    _, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    assert calls[0] == (None, False)
    assert calls[1] == (None, True)
    assert sum(1 for region, discover in calls if region is not None and not discover) == 2
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["region_discovery_done"] is True
    assert row["status"] == "compared_no_candidate"


def test_no_change_is_forbidden_without_required_local_coverage(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [ControlPair(0, 1, 0, 1, 0.9, "text", (), ())])

    def fake_call(state, config, region=None, *, discover_regions=False):
        return {"comparability": "high", "pd_inventory": _inventory("ПД") if region is None and not discover_regions else [], "rd_inventory": _inventory("РД") if region is None and not discover_regions else [], "findings": [], "candidate_regions": [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    _, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "unclear"
    assert row["candidate_regions_checked"] == 0


def test_bare_change_without_two_sided_evidence_is_not_a_finding(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [ControlPair(0, 1, 0, 1, 0.9, "text", (), ())])

    def fake_call(state, config, region=None, *, discover_regions=False):
        if discover_regions:
            return {"comparability": "high", "candidate_regions": _regions(2)}
        return {"comparability": "high", "pd_inventory": _inventory("ПД"), "rd_inventory": _inventory("РД"), "findings": [{"difference": "что-то изменено"}], "candidate_regions": _regions(2) if region is None else [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    signals, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    assert signals == []
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "compared_no_candidate"
    assert row["confirmed_findings"] == []


def test_medium_whole_page_candidate_requires_zoom_before_confirmation(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [ControlPair(0, 1, 0, 1, 0.2, "text", (), ())])

    def fake_call(state, config, region=None, *, discover_regions=False):
        if region is None:
            return {"comparability": "medium", "pd_inventory": _inventory("ПД"), "rd_inventory": _inventory("РД"), "findings": [_grounded_difference("ветка")], "candidate_regions": _regions(1), "uncertainties": ["мелкий масштаб"]}
        return {"comparability": "high", "findings": [_grounded_difference("ветка")], "candidate_regions": [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    signals, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    assert len([s for s in signals if s.source == "vision_pair"]) == 1
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "confirmed_difference"
    assert row["candidate_regions_checked"] == 1


def test_zoom_scheduling_is_round_robin(monkeypatch):
    pairs = [ControlPair(0, 1, 0, 1, 0.2, "text", (), ()), ControlPair(0, 1, 1, 1, 0.2, "text", (), ())]
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: pairs)
    order = []

    def fake_call(state, config, region=None, *, discover_regions=False):
        order.append((state.after_path, region is not None, discover_regions))
        if region is None:
            return {"comparability": "high", "pd_inventory": _inventory("ПД"), "rd_inventory": _inventory("РД"), "findings": [], "candidate_regions": _regions(2), "uncertainties": []}
        return {"comparability": "high", "findings": [], "candidate_regions": [], "uncertainties": []}

    monkeypatch.setattr(runtime, "_call_model", fake_call)
    runtime.run_targeted_pair_vision([_doc("pd")], [_doc("a"), _doc("b")], ["pd.pdf"], ["a.pdf", "b.pdf"], object(), max_pairs=2)
    assert order[:2] == [("a.pdf", False, False), ("b.pdf", False, False)]
    zoom_order = [item[0] for item in order if item[1]]
    assert zoom_order[:4] == ["a.pdf", "b.pdf", "a.pdf", "b.pdf"]


def test_low_comparability_never_becomes_no_difference(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [ControlPair(0, 1, 0, 1, 0.8, "text", (), ())])
    monkeypatch.setattr(runtime, "_call_model", lambda *args, **kwargs: {"comparability": "low", "pd_inventory": [], "rd_inventory": [], "findings": [], "candidate_regions": [], "uncertainties": ["не читается"]})
    _, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    row = next(x for x in diagnostics if x.get("control_type") == "page_pair")
    assert row["status"] == "unclear"


def test_coverage_reports_legacy_orchestrators_inactive(monkeypatch):
    monkeypatch.setattr(runtime, "candidate_pairs", lambda *args: [])
    _, diagnostics = runtime.run_targeted_pair_vision([_doc("pd")], [_doc("rd")], ["pd.pdf"], ["rd.pdf"], object(), max_pairs=1)
    coverage = diagnostics[-1]
    assert coverage["legacy_room_runtime_active"] is False
    assert coverage["legacy_generic_region_runtime_active"] is False
    assert coverage["raster_gate_active"] is False
    assert coverage["whole_page_no_change_allowed"] is False
