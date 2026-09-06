"""HTTP-эндпоинт `/triangulated-runs` (см. `main.py`) вызывает
`triangulated_pipeline.run_triangulated_analysis()` — это её тест, без
реального комплекта (Г.12: только синтетические данные, не `nadzor_sample`).

Синтетика построена по формату, который `rooms.py`/`test_rooms.py` уже
считают реальным: номер помещения, кириллическое название, площадь —
каждое на отдельной текстовой строке (см. `test_rooms.py`,
`extract_room_facts`).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
import pytest

from app.llm import LlmConfig
from app.triangulated_pipeline import run_triangulated_analysis


_CYRILLIC_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _make_pdf(path: Path, lines: list[str]) -> None:
    """Синтетический однострочный-на-строку PDF — встроенный шрифт
    Helvetica (стандартный у PyMuPDF) не несёт кириллицу вообще (текст
    рендерится точками, `get_text` вернул бы мусор вместо названия
    помещения), поэтому шрифт с кириллицей передаётся явно."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontsize=11, fontfile=_CYRILLIC_FONT, fontname="F0")
        y += 16
    doc.save(str(path))
    doc.close()


def test_invalid_run_when_a_side_has_no_readable_documents(tmp_path):
    result = run_triangulated_analysis([str(tmp_path / "missing.pdf")], [str(tmp_path / "also_missing.pdf")])
    assert result["valid"] is False
    assert "ПД" in result["reason"] or "РД" in result["reason"]
    print("OK: обе стороны пустые -> прогон честно помечен недействительным, не пустым отчётом")


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей для синтетического PDF")
def test_room_missing_in_rd_produces_an_escalation_ticket_with_display_names(tmp_path):
    """Синтетический ПД содержит помещение 301, синтетический РД — нет.
    Один источник (room_registry) -> кандидат, не подтверждено -> попадает
    в очередь эскалации (Г.30 п.5)."""
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["302", "Учебный кабинет", "40.0"])

    result = run_triangulated_analysis(
        [str(pd_path)], [str(rd_path)],
        before_names=["ПД Раздел 1.pdf"], after_names=["РД Раздел 1.pdf"],
    )

    assert result["valid"] is True
    # Отображаемые имена — те, что переданы явно, а не случайные имена
    # файлов на диске (см. докстринг `_load_documents`).
    assert result["documents"] == {"before": ["ПД Раздел 1.pdf"], "after": ["РД Раздел 1.pdf"]}
    assert result["llm"]["used"] is False
    assert any("requirements_llm_extract" in n for n in result["not_run"])
    assert any("routing_diff" in n for n in result["not_run"])

    room_findings = result["rooms"]["findings"]
    assert any(f["room_key"] == "301" and f["finding_type"] == "missing_in_rd" for f in room_findings), room_findings

    tickets = result["escalation_tickets"]
    ticket = next((t for t in tickets if t["domain"] == "room" and t["key"] == "301"), None)
    assert ticket is not None, tickets
    assert ticket["sources_present"] == ["room_registry"]
    assert "room_registry" not in ticket["sources_missing"]

    # Ничего confirmed — только один источник существует в этом синтетическом прогоне.
    assert result["triangulation"]["confirmed"] == []
    print("OK: расхождение по синтетическому помещению 301 дошло до очереди эскалации "
          "с реальными переданными именами документов, не с UUID-путями на диске")


@pytest.mark.skipif(not Path(_CYRILLIC_FONT).is_file(), reason="нет системного шрифта с кириллицей для синтетического PDF")
def test_general_requirements_go_through_llm_filter_when_key_present(tmp_path, monkeypatch):
    """Реальный найденный пробел (Г.75): этот эндпоинт (используется
    /triangulated-runs -> AttentionMap.tsx, то есть САМ СЕРВИС, не только
    CLI-скрипт registry_diff.py) извлекал каталог формы 3 регуляркой и
    НИКОГДА не прогонял его через ЛЛМ-фильтр шума (Г.69/70), даже когда
    ключ ИИ уже используется для соседних шагов того же прогона — молчаливый
    пробел, не отражённый в `not_run` в отличие от честно помеченных
    ventilation_mo/page_pair_comparison."""
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0", "Экраны должны быть негорючими."])
    _make_pdf(rd_path, ["301", "Школьный зал", "50.0"])

    import app.triangulated_pipeline as pipeline
    from app.requirement_llm_filter import RequirementVerdict

    monkeypatch.setattr(pipeline, "extract_requirements_llm", lambda text_facts, config: [])

    def fake_classify(requirements, config):
        return [RequirementVerdict(requirement=r, is_requirement=True, reasoning="ok") for r in requirements]

    monkeypatch.setattr(pipeline, "classify_general_requirements", fake_classify)

    config = LlmConfig(provider="anthropic", api_key="fake-key-for-test")
    result = run_triangulated_analysis([str(pd_path)], [str(rd_path)], llm_config=config)

    assert result["llm"]["used"] is True
    assert not any("requirement_llm_filter" in n for n in result["not_run"]), (
        "с ключом ИИ фильтр обязан запускаться, а не попадать в список непрогнанного"
    )
    general = result["requirements"]["general"]["llm_filter"]
    assert general["used"] is True
    assert general["kept"] == 1
    assert general["dropped_as_noise"] == []
    print("OK: с ключом ИИ каталог формы 3 реально проходит через requirement_llm_filter, "
          "а не молча извлекается только регуляркой")


def test_general_requirements_stay_regex_only_without_llm_key(tmp_path):
    """Без ключа — как раньше: каталог формы 3 остаётся regex-путём, и это
    честно видно в `not_run`, не молчит."""
    pd_path = tmp_path / "AAAA.pdf"
    rd_path = tmp_path / "BBBB.pdf"
    _make_pdf(pd_path, ["301", "Школьный зал", "50.0"])
    _make_pdf(rd_path, ["301", "Школьный зал", "50.0"])

    result = run_triangulated_analysis([str(pd_path)], [str(rd_path)])

    assert result["llm"]["used"] is False
    assert any("requirement_llm_filter" in n for n in result["not_run"])
    assert result["requirements"]["general"]["llm_filter"]["used"] is False
    assert result["requirements"]["general"]["llm_filter"]["kept"] is None
    print("OK: без ключа ИИ фильтр честно помечен как непрогнанный, каталог не выдаётся за отфильтрованный")
