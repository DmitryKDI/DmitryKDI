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
