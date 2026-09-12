from __future__ import annotations

import app.generic_region_vision as generic
from app.generic_region_vision import (
    RegionProposal,
    _fp_distance,
    _normalize_clip,
    _normalize_pass1,
    _proposal_priority,
)


def _region(kind: str, confidence: float = 0.5, suffix: str = "1") -> RegionProposal:
    return RegionProposal(
        region_id=f"{kind}:{suffix}",
        anchor_type=kind,
        anchor_ids=(),
        pd_clip=(0.1, 0.1, 0.4, 0.4),
        rd_clip=(0.2, 0.2, 0.5, 0.5),
        pair_confidence=confidence,
        provenance="test_navigation_only",
    )


def test_normalize_clip_clamps_and_rejects_tiny_boxes():
    assert _normalize_clip([-0.1, 0.2, 1.2, 0.8]) == (0.0, 0.2, 1.0, 0.8)
    assert _normalize_clip([0.2, 0.2, 0.21, 0.8]) is None
    assert _normalize_clip([0.2, 0.2, "bad", 0.8]) is None


def test_geometry_fingerprint_distance_is_coordinate_free_metric():
    a = (0.1, 0.2, 0.3, 0.4)
    assert _fp_distance(a, a) == 0.0
    assert 0.0 < _fp_distance(a, (0.2, 0.3, 0.4, 0.5)) < 1.0
    assert _fp_distance(a, (0.1, 0.2)) == 1.0


def test_pass1_normalization_preserves_uncertainty_and_navigation_provenance():
    region = _region("geometry", 0.61)
    result = _normalize_pass1(
        {
            "comparability": "medium",
            "candidate_differences": [
                {
                    "category": "connection",
                    "description": "видимое изменение подключения",
                    "location_hint": "локальная зона",
                }
            ],
            "uncertainty_reasons": ["частичный crop"],
            "topology_observations": ["ветвление видно частично"],
        },
        region,
    )
    assert result["status"] == "changed_candidate"
    assert result["comparability"] == "medium"
    assert result["anchor_provenance"] == "test_navigation_only"
    assert result["candidate_differences"][0]["category"] == "connection"
    assert result["uncertainty_reasons"] == ["частичный crop"]


def test_region_priority_prefers_axis_then_equipment_then_geometry_then_adaptive():
    regions = [
        _region("adaptive_generic", 0.9),
        _region("geometry", 0.9),
        _region("equipment_system", 0.6),
        _region("axis", 0.5),
    ]
    ordered = sorted(regions, key=_proposal_priority)
    assert [item.anchor_type for item in ordered] == [
        "axis",
        "equipment_system",
        "geometry",
        "adaptive_generic",
    ]


def test_failed_discovery_calls_consume_one_budget_slot_each(monkeypatch):
    proposals = [
        _region("axis", 0.8, "a"),
        _region("equipment_system", 0.7, "b"),
    ]
    monkeypatch.setattr(generic, "propose_regions", lambda *args, **kwargs: proposals)
    monkeypatch.setattr(
        generic,
        "_view",
        lambda *args, **kwargs: {"region": args[-1]},
    )

    def fail_pass1(*args, **kwargs):
        raise RuntimeError("provider failure")

    monkeypatch.setattr(generic, "_call_pass1", fail_pass1)

    findings, diagnostics, used = generic.compare_non_room_regions(
        "pd.pdf",
        1,
        "rd.pdf",
        1,
        (),
        None,  # provider never reached because _call_pass1 is monkeypatched
        max_calls=4,
        discipline="ОВ",
    )

    assert findings == []
    assert used == 2
    errors = [row for row in diagnostics if row.get("status") == "generic_region_pass1_error"]
    assert len(errors) == 2
    budget = next(row for row in diagnostics if row.get("status") == "generic_region_budget")
    assert budget["calls_used"] == 2
    assert budget["calls_budget"] == 4
