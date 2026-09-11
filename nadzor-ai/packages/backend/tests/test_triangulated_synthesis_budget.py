"""Регрессия стоимости «Точек контроля»: одиночный источник не должен
порождать отдельный LLM-свод на каждый candidate.
"""
from pathlib import Path

import pymupdf
import pytest

from app.llm import LlmConfig
from app.triangulation import Signal
import app.triangulated_pipeline as pipeline


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


def _disable_unrelated_llm(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "extract_requirements_llm", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "classify_general_requirements", lambda *args, **kwargs: [])
    monkeypatch.setattr(pipeline, "diff_room_routing", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline, "signals_from_routing_diff", lambda *args, **kwargs: [])


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет шрифта с кириллицей")
def test_single_source_candidates_do_not_trigger_verdict_llm(tmp_path, monkeypatch):
    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["302", "Учебный кабинет", "40.0"])
    _disable_unrelated_llm(monkeypatch)

    def forbidden_synthesis(*args, **kwargs):
        raise AssertionError("synthesize_all не должен вызываться для single-source candidates")

    monkeypatch.setattr(pipeline, "synthesize_all", forbidden_synthesis)
    config = LlmConfig(provider="anthropic", api_key="fake-key")

    result = pipeline.run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)], llm_config=config,
    )

    assert result["triangulation"]["confirmed"] == []
    assert result["triangulation"]["candidates"]
    assert result["verdicts"] == []
    assert any("нет объектов, подтверждённых 2+" in item for item in result["not_run"])


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет шрифта с кириллицей")
def test_confirmed_keys_are_the_only_keys_sent_to_synthesis(tmp_path, monkeypatch):
    pd_path = tmp_path / "pd.pdf"
    rd_path = tmp_path / "rd.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["302", "Учебный кабинет", "40.0"])
    _disable_unrelated_llm(monkeypatch)

    monkeypatch.setattr(
        pipeline,
        "signals_from_room_cross_check",
        lambda findings: [
            Signal(source="room_registry", domain="room", key="301", detail="помещение отсутствует"),
            Signal(source="vision", domain="room", key="301", detail="на листе РД помещение не найдено"),
            Signal(source="room_registry", domain="room", key="999", detail="одиночный шум"),
        ],
    )

    captured: dict = {}

    def fake_synthesize(signals, config, only_keys=None, **kwargs):
        captured["only_keys"] = only_keys
        return []

    monkeypatch.setattr(pipeline, "synthesize_all", fake_synthesize)
    config = LlmConfig(provider="anthropic", api_key="fake-key")

    result = pipeline.run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)], llm_config=config,
    )

    assert captured["only_keys"] == {("room", "301")}
    confirmed = {(item["domain"], item["key"]) for item in result["triangulation"]["confirmed"]}
    candidates = {(item["domain"], item["key"]) for item in result["triangulation"]["candidates"]}
    assert ("room", "301") in confirmed
    assert ("room", "999") in candidates
