"""Regression tests for the active stateful investigator facade."""
from types import SimpleNamespace

from app.llm import LlmConfig
from app.matching import DocumentInput
from app.requirement_registry import Requirement
from app.stateful_investigator import InvestigatorResult
from app.triangulated_pipeline import run_triangulated_analysis
import app.lean_analysis_runtime as lean


def _doc(name: str) -> DocumentInput:
    return DocumentInput(name=name, pages=1, page_kinds={1: "drawing"})


def test_invalid_run_when_a_side_has_no_readable_documents(tmp_path):
    result = run_triangulated_analysis(
        [str(tmp_path / "missing.pdf")],
        [str(tmp_path / "also_missing.pdf")],
    )
    assert result["valid"] is False
    assert "ПД" in result["reason"] or "РД" in result["reason"]
    assert result["performance"]["duration_seconds"] >= 0
    assert result["active_architecture"] == "stateful_investigator"


def test_facade_uses_one_stateful_investigator(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult(
            [_doc("pd") if "pd" in paths[0] else _doc("rd")], []
        ),
    )
    monkeypatch.setattr(
        lean,
        "_load_text_facts",
        lambda *args, **kwargs: [
            {"fact_id": 1, "page": 1, "text": "текст", "document": "doc", "section": ""}
        ],
    )
    requirement = Requirement(
        rooms=["101"], page=1, sentence="Предусмотрена система X", summary="Система X"
    )
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [requirement])

    calls = []

    def fake_investigator(*args, **kwargs):
        calls.append((args, kwargs))
        return InvestigatorResult(
            findings=[
                {
                    "source": "stateful_investigator",
                    "domain": "semantic",
                    "key": "PD0:P1|RD0:P1",
                    "detail": "A -> B",
                    "requirement_ids": ["R1"],
                }
            ],
            candidates=[],
            diagnostics={
                "architecture": "stateful_investigator->python_tools->self_review->independent_verifier",
                "finished": True,
                "self_reviewed": True,
                "turns_used": 4,
                "max_turns": 14,
                "pages_total": 2,
                "pages_inspected": 2,
                "verification": [],
            },
        )

    monkeypatch.setattr(lean, "run_stateful_investigator", fake_investigator)

    result = run_triangulated_analysis(
        ["pd.pdf"],
        ["rd.pdf"],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert result["valid"] is True
    assert len(calls) == 1
    assert result["semantic_findings"][0]["source"] == "stateful_investigator"
    assert result["semantic_contract"]["stateful_history"] is True
    assert result["semantic_contract"]["independent_verifier_required"] is True
    assert result["requirements"]["compliance"]["items"][0]["confirmed_contradiction"] is True
    assert result["triangulation"]["active"] is False
    assert all(value is False for value in result["legacy_runtime"].values())


def test_room_keys_do_not_gate_stateful_execution(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult(
            [_doc("pd") if "pd" in paths[0] else _doc("rd")], []
        ),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [])
    calls = []

    def fake_investigator(*args, **kwargs):
        calls.append(True)
        return InvestigatorResult(
            diagnostics={
                "finished": True,
                "self_reviewed": True,
                "turns_used": 1,
                "max_turns": 14,
                "pages_total": 2,
                "pages_inspected": 0,
                "verification": [],
            }
        )

    monkeypatch.setattr(lean, "run_stateful_investigator", fake_investigator)
    config = LlmConfig(provider="anthropic", api_key="fake-key")

    first = run_triangulated_analysis(["pd.pdf"], ["rd.pdf"], room_keys=[], llm_config=config)
    second = run_triangulated_analysis(
        ["pd.pdf"], ["rd.pdf"], room_keys=["999"], llm_config=config
    )

    assert len(calls) == 2
    assert first["active_architecture"] == second["active_architecture"]
    assert first["routing"] is None and second["routing"] is None


def test_without_llm_key_investigator_does_not_run(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult(
            [_doc("pd") if "pd" in paths[0] else _doc("rd")], []
        ),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "extract_requirements", lambda *args, **kwargs: [])

    result = run_triangulated_analysis(["pd.pdf"], ["rd.pdf"])

    assert result["llm"]["used"] is False
    assert result["investigator"]["status"] == "not_run"
    assert result["pair_vision"]["results"] == []
    assert result["triangulation"]["active"] is False
