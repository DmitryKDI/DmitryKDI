"""Тесты HTTP-пайплайна точек контроля без реального провайдера."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
import pytest

from app.llm import LlmConfig
from app.requirement_registry import Requirement
from app.triangulated_pipeline import run_triangulated_analysis


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


def test_invalid_run_when_a_side_has_no_readable_documents(tmp_path):
    result = run_triangulated_analysis(
        [str(tmp_path / "missing.pdf")],
        [str(tmp_path / "also_missing.pdf")],
    )
    assert result["valid"] is False
    assert "ПД" in result["reason"] or "РД" in result["reason"]
    assert result["performance"]["duration_seconds"] >= 0


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей")
def test_room_missing_in_rd_produces_escalation_ticket(tmp_path):
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["302", "Учебный кабинет", "40.0"])

    result = run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)],
        before_names=["ПД Раздел 1.pdf"], after_names=["РД Раздел 1.pdf"],
    )

    assert result["valid"] is True
    assert result["documents"] == {
        "before": ["ПД Раздел 1.pdf"],
        "after": ["РД Раздел 1.pdf"],
    }
    assert result["llm"]["used"] is False
    assert any("requirements_llm_extract" in item for item in result["not_run"])

    room_findings = result["rooms"]["findings"]
    assert any(
        finding["room_key"] == "301" and finding["finding_type"] == "missing_in_rd"
        for finding in room_findings
    )
    tickets = result["escalation_tickets"]
    ticket = next((item for item in tickets if item["domain"] == "room" and item["key"] == "301"), None)
    assert ticket is not None
    assert ticket["sources_present"] == ["room_registry"]
    assert result["triangulation"]["confirmed"] == []


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей")
def test_general_requirements_go_through_llm_filter_when_key_present(tmp_path, monkeypatch):
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0", "Экраны должны быть негорючими."])
    _make_pdf(rd_path, ["301", "Школьный зал", "50.0"])

    import app.triangulated_pipeline as pipeline
    from app.requirement_llm_filter import RequirementVerdict

    monkeypatch.setattr(pipeline, "extract_requirements_llm", lambda *args, **kwargs: [])

    def fake_classify(requirements, config, **kwargs):
        return [
            RequirementVerdict(requirement=requirement, is_requirement=True, reasoning="ok")
            for requirement in requirements
        ]

    monkeypatch.setattr(pipeline, "classify_general_requirements", fake_classify)
    monkeypatch.setattr(pipeline, "diff_room_routing", lambda *args, **kwargs: {})

    config = LlmConfig(provider="anthropic", api_key="fake-key-for-test")
    result = run_triangulated_analysis([str(pd_path)], [str(rd_path)], llm_config=config)

    assert result["llm"]["used"] is True
    assert not any("requirement_llm_filter" in item for item in result["not_run"])
    general = result["requirements"]["general"]["llm_filter"]
    assert general["used"] is True
    assert general["kept"] == 1
    assert general["dropped_as_noise"] == []


def test_general_requirements_stay_regex_only_without_llm_key(tmp_path):
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["301", "Школьный зал", "50.0"])

    result = run_triangulated_analysis([str(pd_path)], [str(rd_path)])

    assert result["llm"]["used"] is False
    assert any("requirement_llm_filter" in item for item in result["not_run"])
    assert result["requirements"]["general"]["llm_filter"]["used"] is False
    assert result["requirements"]["general"]["llm_filter"]["kept"] is None


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей")
def test_targeted_vision_absence_confirms_requirement(tmp_path, monkeypatch):
    """Требование из ПД + absent по листу РД = два независимых источника."""
    import app.triangulated_pipeline as pipeline

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    _make_pdf(pd_path, ["270", "Санузел МГН", "Предусмотрена система подогрева пола"])
    _make_pdf(rd_path, ["270", "Санузел МГН", "План отопления"])

    requirement = Requirement(
        rooms=["270"],
        page=1,
        sentence="В помещении 270 предусмотрена система подогрева пола",
        code=None,
    )
    monkeypatch.setattr(pipeline, "extract_requirements_llm", lambda *args, **kwargs: [requirement])
    monkeypatch.setattr(pipeline, "extract_general_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "classify_general_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        pipeline,
        "check_visual_candidates",
        lambda findings, room_index, config, **kwargs: [
            {
                "rooms": ["270"],
                "sentence": requirement.sentence,
                "verdict": "absent",
                "reason": "на листе РД система подогрева пола не показана",
                "where": "помещение 270",
                "pages_checked": 1,
            }
        ],
    )
    monkeypatch.setattr(pipeline, "diff_room_routing", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline, "synthesize_all", lambda *args, **kwargs: [])

    result = run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    confirmed = {
        (item["domain"], item["key"]): set(item["sources"])
        for item in result["triangulation"]["confirmed"]
    }
    assert confirmed[("room", "270")] == {"requirement_prose", "vision"}
    assert result["vision_requirements"]["checked_total"] == 1
    assert result["vision_requirements"]["counts"]["absent"] == 1
    assert result["performance"]["stages_seconds"]["targeted_requirement_vision"] >= 0


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей")
def test_general_llm_filter_changes_actual_cross_check_input(tmp_path, monkeypatch):
    """Отфильтрованный шум не должен всё равно попадать в cross-check."""
    import app.triangulated_pipeline as pipeline
    from app.requirement_llm_filter import RequirementVerdict

    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    _make_pdf(pd_path, ["301", "Помещение", "50.0"])
    _make_pdf(rd_path, ["301", "Помещение", "50.0"])

    keep = Requirement(rooms=[], page=1, sentence="Применить материал «X».", code=None)
    drop = Requirement(rooms=[], page=1, sentence="Раздел разработан в соответствии с нормами.", code=None)
    monkeypatch.setattr(pipeline, "extract_requirements_llm", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "extract_general_requirements", lambda *args, **kwargs: [keep, drop])
    monkeypatch.setattr(
        pipeline,
        "classify_general_requirements",
        lambda *args, **kwargs: [
            RequirementVerdict(requirement=keep, is_requirement=True, reasoning="технический факт"),
            RequirementVerdict(requirement=drop, is_requirement=False, reasoning="мета-текст"),
        ],
    )
    monkeypatch.setattr(pipeline, "diff_room_routing", lambda *args, **kwargs: {})

    result = run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)],
        llm_config=LlmConfig(provider="anthropic", api_key="fake-key"),
    )

    assert result["requirements"]["general"]["raw_total"] == 2
    assert result["requirements"]["general"]["total"] == 1
    assert result["requirements"]["general"]["llm_filter"]["kept"] == 1
