"""The active lean runtime must never call legacy synthesis/triangulation."""
from types import SimpleNamespace

from app.llm import LlmConfig
from app.matching import DocumentInput
import app.lean_analysis_runtime as lean
import app.triangulated_pipeline as pipeline


def _doc(name: str) -> DocumentInput:
    return DocumentInput(name=name, pages=1, page_kinds={1: "drawing"})


def _prepare(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult(
            [_doc("pd") if "pd" in paths[0] else _doc("rd")], []
        ),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "_room_index", lambda *args, **kwargs: {})
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        lean,
        "check_requirements_semantic",
        lambda *args, **kwargs: SimpleNamespace(counts={}, not_run=[], diagnostics={}, items=[]),
    )


def test_active_runtime_does_not_run_legacy_synthesis(monkeypatch):
    _prepare(monkeypatch)
    pair_calls = []
    monkeypatch.setattr(
        lean,
        "run_targeted_pair_vision",
        lambda *args, **kwargs: (pair_calls.append(True) or ([], [{"control_type": "coverage", "status": "complete"}])),
    )

    result = pipeline.run_triangulated_analysis(
        ["pd.pdf"], ["rd.pdf"],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert pair_calls == [True]
    assert result["verdicts"] == []
    assert result["escalation_tickets"] == []
    assert result["triangulation"]["active"] is False
    assert result["legacy_runtime"]["verdict_synthesis"] is False
    assert result["legacy_runtime"]["mandatory_triangulation"] is False
    assert result["legacy_runtime"]["legacy_compliance_ladder"] is False


def test_registry_and_routing_branches_are_not_part_of_active_result(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        lean,
        "run_targeted_pair_vision",
        lambda *args, **kwargs: ([], [{"control_type": "coverage", "status": "complete"}]),
    )

    result = pipeline.run_triangulated_analysis(
        ["pd.pdf"], ["rd.pdf"],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert result["rooms"]["active"] is False
    assert result["equipment"]["active"] is False
    assert result["composition"]["active"] is False
    assert result["routing"] is None
    assert result["legacy_runtime"]["room_registry"] is False
    assert result["legacy_runtime"]["equipment_registry"] is False
    assert result["legacy_runtime"]["composition_registry"] is False
    assert result["legacy_runtime"]["routing_diff"] is False
    assert result["legacy_runtime"]["general_requirement_filter"] is False
