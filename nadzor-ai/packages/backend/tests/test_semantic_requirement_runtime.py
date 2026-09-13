from __future__ import annotations

from types import SimpleNamespace

import app.semantic_requirement_runtime as runtime
from app.compliance import STATUS_CONFIRMED, STATUS_NEEDS_CHECK
from app.requirement_registry import Requirement


def _req() -> Requirement:
    return Requirement(rooms=[], page=1, sentence="Предусмотреть систему X", document="pd.pdf", section="ОВ")


def _pool():
    return [{"path": "rd.pdf", "page": 1, "text": "лист РД", "name": "rd.pdf"}]


def _config():
    return SimpleNamespace(api_key="fake-key")


def _region():
    return {"reason": "узел X", "rd_bbox_norm": [0.1, 0.1, 0.35, 0.35], "priority": "high"}


def _finding(kind="configuration"):
    return {
        "state": "OBSERVED_CONTRADICTION",
        "pd_claim": "ПД требует X=A",
        "pd_evidence_ref": "PD requirement text",
        "rd_claim": "РД показывает X=B",
        "rd_evidence_ref": "RD local bbox",
        "difference": "X A -> B",
        "difference_kind": kind,
        "evidence_scope": "complete",
        "absence_verified": False,
        "corroboration_refs": [],
        "requires_additional_evidence": False,
    }


def test_requirement_compliance_needs_local_evidence(monkeypatch):
    monkeypatch.setattr(runtime, "rd_page_pool", lambda sources: _pool())
    monkeypatch.setattr(runtime, "rank_pool_for_requirement", lambda pool, sentence, max_pages: pool[:1])

    def fake_call(config, req, entry, *, region=None, discover=False):
        if region is None and not discover:
            return {
                "evidence_state": "APPEARS_COMPLIANT",
                "comparability": "high",
                "requirement_evidence": {"observed": True, "rd_evidence_ref": "whole page"},
                "candidate_regions": [_region()],
            }
        return {
            "evidence_state": "APPEARS_COMPLIANT",
            "comparability": "high",
            "requirement_evidence": {"observed": True, "rd_evidence_ref": "local symbol X", "where": "zone"},
        }

    monkeypatch.setattr(runtime, "_call", fake_call)
    result = runtime.check_requirements_semantic([_req()], [("rd.pdf", "rd.pdf")], _config())
    assert result.items[0].status == STATUS_CONFIRMED
    row = result.diagnostics["results"][0]
    assert row["confirmed_evidence"]["bbox"] == (0.1, 0.1, 0.35, 0.35)
    assert result.diagnostics["zoom_calls"] == 1


def test_whole_page_absence_is_not_final_without_local_and_scope(monkeypatch):
    monkeypatch.setattr(runtime, "rd_page_pool", lambda sources: _pool())
    monkeypatch.setattr(runtime, "rank_pool_for_requirement", lambda pool, sentence, max_pages: pool[:1])
    absence = {
        **_finding("presence_absence"),
        "rd_claim": "X не наблюдается",
        "difference": "X отсутствует",
        "absence_verified": False,
        "evidence_scope": "partial",
        "requires_additional_evidence": True,
    }

    def fake_call(config, req, entry, *, region=None, discover=False):
        if region is None and not discover:
            return {
                "evidence_state": "OBSERVED_CONTRADICTION",
                "comparability": "high",
                "findings": [absence],
                "candidate_regions": [_region()],
            }
        return {
            "evidence_state": "NOT_OBSERVED_ON_THIS_EVIDENCE",
            "comparability": "high",
            "findings": [],
            "additional_evidence_needed": ["другой вид/спецификация"],
        }

    monkeypatch.setattr(runtime, "_call", fake_call)
    result = runtime.check_requirements_semantic([_req()], [("rd.pdf", "rd.pdf")], _config())
    assert result.items[0].status == STATUS_NEEDS_CHECK
    assert "не означает" in result.items[0].detail.casefold()
    row = result.diagnostics["results"][0]
    assert row["final_state"] == "NOT_OBSERVED_ON_THIS_EVIDENCE"


def test_wrong_scope_is_not_compliant(monkeypatch):
    monkeypatch.setattr(runtime, "rd_page_pool", lambda sources: _pool())
    monkeypatch.setattr(runtime, "rank_pool_for_requirement", lambda pool, sentence, max_pages: pool[:1])
    monkeypatch.setattr(runtime, "_call", lambda *args, **kwargs: {
        "evidence_state": "WRONG_OR_INSUFFICIENT_SCOPE",
        "comparability": "low",
        "candidate_regions": [],
        "additional_evidence_needed": ["нужен план"],
    })
    result = runtime.check_requirements_semantic([_req()], [("rd.pdf", "rd.pdf")], _config())
    assert result.items[0].status == STATUS_NEEDS_CHECK
    assert result.diagnostics["vision_unclear"] == 1
