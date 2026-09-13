"""Regression tests for the lean active PD -> RD analysis facade."""
from types import SimpleNamespace
from pathlib import Path

import pymupdf

from app.llm import LlmConfig
from app.matching import DocumentInput
from app.requirement_registry import Requirement
from app.triangulated_pipeline import run_triangulated_analysis
import app.lean_analysis_runtime as lean


_CYRILLIC_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _make_pdf(path: Path, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=11, fontfile=_CYRILLIC_FONT, fontname="F0")
        y += 16
    doc.save(str(path))
    doc.close()


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
    assert result["active_architecture"] == "requirement_check + semantic_pair_runtime"


def test_facade_uses_only_lean_semantic_tracks(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult([_doc("pd") if "pd" in paths[0] else _doc("rd")], []),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [{"fact_id": 1, "page": 1, "text": "текст", "document": "doc", "section": ""}])
    monkeypatch.setattr(lean, "_room_index", lambda *args, **kwargs: {})

    requirement = Requirement(rooms=[], page=1, sentence="Предусмотрена система X", code=None)
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [requirement])

    compliance = SimpleNamespace(
        counts={"требует проверки": 1},
        not_run=[],
        diagnostics={"vision_calls": 1},
        items=[],
    )
    compliance_calls = []

    def fake_compliance(*args, **kwargs):
        compliance_calls.append((args, kwargs))
        return compliance

    monkeypatch.setattr(lean, "check_compliance", fake_compliance)
    pair_calls = []

    def fake_pair(*args, **kwargs):
        pair_calls.append((args, kwargs))
        signal = SimpleNamespace(source="vision_pair", domain="page_pair", key="0:1->0:1", detail="A -> B")
        return [signal], [
            {
                "control_type": "page_pair",
                "pair_key": "0:1->0:1",
                "status": "confirmed_difference",
                "pd_inventory": [{"entity": "A"}],
                "rd_inventory": [{"entity": "B"}],
            },
            {"control_type": "coverage", "status": "complete"},
        ]

    monkeypatch.setattr(lean, "run_targeted_pair_vision", fake_pair)

    result = run_triangulated_analysis(
        ["pd.pdf"], ["rd.pdf"],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert result["valid"] is True
    assert len(compliance_calls) == 1
    assert len(pair_calls) == 1
    assert result["semantic_findings"][0]["source"] == "vision_pair"
    assert result["pair_vision"]["counts"]["confirmed_difference"] == 1
    assert result["triangulation"]["active"] is False
    assert result["legacy_runtime"] == {
        "room_registry": False,
        "equipment_registry": False,
        "composition_registry": False,
        "routing_diff": False,
        "general_requirement_filter": False,
        "verdict_synthesis": False,
        "mandatory_triangulation": False,
    }


def test_room_keys_do_not_gate_or_change_semantic_execution(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult([_doc("pd") if "pd" in paths[0] else _doc("rd")], []),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "_room_index", lambda *args, **kwargs: {})
    monkeypatch.setattr(lean, "extract_requirements_llm", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        lean,
        "check_compliance",
        lambda *args, **kwargs: SimpleNamespace(counts={}, not_run=[], diagnostics={}, items=[]),
    )
    calls = []
    monkeypatch.setattr(
        lean,
        "run_targeted_pair_vision",
        lambda *args, **kwargs: (calls.append(True) or ([], [{"control_type": "coverage", "status": "complete"}])),
    )

    config = LlmConfig(provider="anthropic", api_key="fake-key")
    first = run_triangulated_analysis(["pd.pdf"], ["rd.pdf"], room_keys=[], llm_config=config)
    second = run_triangulated_analysis(["pd.pdf"], ["rd.pdf"], room_keys=["999"], llm_config=config)

    assert len(calls) == 2
    assert first["active_architecture"] == second["active_architecture"]
    assert first["routing"] is None and second["routing"] is None


def test_without_llm_key_legacy_branches_stay_inactive(monkeypatch):
    monkeypatch.setattr(
        lean,
        "_load_documents",
        lambda paths, names=None: lean.DocumentLoadResult([_doc("pd") if "pd" in paths[0] else _doc("rd")], []),
    )
    monkeypatch.setattr(lean, "_load_text_facts", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "extract_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(lean, "_room_index", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        lean,
        "check_compliance",
        lambda *args, **kwargs: SimpleNamespace(counts={}, not_run=["нет ключа ИИ"], diagnostics={}, items=[]),
    )

    result = run_triangulated_analysis(["pd.pdf"], ["rd.pdf"])

    assert result["llm"]["used"] is False
    assert result["pair_vision"]["results"] == []
    assert result["triangulation"]["active"] is False
    assert all(value is False for value in result["legacy_runtime"].values())
