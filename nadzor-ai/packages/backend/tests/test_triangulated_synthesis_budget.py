"""Active runtime must stay stateful and keep legacy orchestration inactive."""
from app.llm import LlmConfig
from app.matching import DocumentInput
from app.stateful_investigator import InvestigatorResult
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
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [])


def test_active_runtime_runs_one_stateful_investigator_not_legacy_synthesis(monkeypatch):
    _prepare(monkeypatch)
    calls = []

    def fake_investigator(*args, **kwargs):
        calls.append(True)
        return InvestigatorResult(
            diagnostics={
                "architecture": "stateful_investigator->python_tools->self_review->independent_verifier",
                "finished": True,
                "self_reviewed": True,
                "turns_used": 2,
                "max_turns": 24,
                "pages_total": 2,
                "pages_inspected": 2,
                "verification": [],
            }
        )

    monkeypatch.setattr(lean, "run_stateful_investigator", fake_investigator)

    result = pipeline.run_triangulated_analysis(
        ["pd.pdf"], ["rd.pdf"],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert calls == [True]
    assert result["verdicts"] == []
    assert result["escalation_tickets"] == []
    assert result["triangulation"]["active"] is False
    assert result["legacy_runtime"]["verdict_synthesis"] is False
    assert result["legacy_runtime"]["mandatory_triangulation"] is False
    assert result["legacy_runtime"]["legacy_compliance_ladder"] is False
    assert result["legacy_runtime"]["isolated_pair_micro_prompts"] is False
    assert result["legacy_runtime"]["isolated_requirement_micro_prompts"] is False


def test_registry_and_routing_branches_are_not_part_of_active_result(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        lean,
        "run_stateful_investigator",
        lambda *args, **kwargs: InvestigatorResult(
            diagnostics={
                "finished": True,
                "self_reviewed": True,
                "turns_used": 1,
                "max_turns": 24,
                "pages_total": 2,
                "pages_inspected": 0,
                "verification": [],
            }
        ),
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
