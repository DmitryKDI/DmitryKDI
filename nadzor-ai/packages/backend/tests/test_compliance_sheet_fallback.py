"""Требование без номера помещения тоже доходит до просмотра листа.

Реальный прогон показал, чем это кончалось: из 8 требований 7 получили
«требует проверки» и ни одно — «подтверждено», потому что подобрать лист
реестр помещений мог не для всех, а без листа дорогой шаг просто не
выполнялся. «Не за что зацепиться реестром» подавалось как результат
проверки, хотя проверки не было (Г.10).

Реестр помещений остаётся способом выбрать ЛУЧШИЙ лист. Когда его нет,
лист берётся по близости текста листа к тексту требования, а лист без
текстового слоя остаётся кандидатом с нулевым весом: подписи чертежа
часто переведены в кривые (Г.8), и отбрасывать такие листы значило бы
выбрасывать ровно ту графику, ради которой проверка и нужна.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from app.compliance import STATUS_CONFIRMED, STATUS_NEEDS_CHECK, check_compliance  # noqa: E402
from app.requirement_registry import Requirement  # noqa: E402
from app.vision_page_compare import rank_pool_for_requirement, rd_page_pool  # noqa: E402

CYRILLIC_TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _pdf(path: Path, pages: list[str]) -> str:
    doc = pymupdf.open()
    font = pymupdf.Font(fontfile=CYRILLIC_TTF)
    for text in pages:
        page = doc.new_page()
        page.insert_font(fontname="F0", fontbuffer=font.buffer)
        if text:
            page.insert_textbox(pymupdf.Rect(36, 36, 560, 780), text,
                                fontname="F0", fontsize=9)
    doc.save(str(path))
    doc.close()
    return str(path)


def _req(sentence, rooms=None):
    return Requirement(rooms=rooms or [], page=1, sentence=sentence)


def test_pool_keeps_sheets_without_text_layer(tmp_path):
    """Лист в кривых — кандидат с нулевым весом, а не исчезнувший лист."""
    path = _pdf(tmp_path / "рд.pdf", ["Схема теплоснабжения установок", ""])

    pool = rd_page_pool([(path, "рд.pdf")])

    assert [entry["page"] for entry in pool] == [1, 2]
    assert pool[1]["text"] == "", "лист без текста обязан остаться в пуле"


def test_requirement_text_picks_the_relevant_sheet(tmp_path):
    """Ранжирование — по редким общим словам, как и при подборе по помещению."""
    path = _pdf(tmp_path / "рд.pdf", [
        "Общие данные. Ведомость чертежей.",
        "План сетей теплоснабжения приточных установок",
        "Экспликация полов",
    ])
    pool = rd_page_pool([(path, "рд.pdf")])

    ordered = rank_pool_for_requirement(pool, "Теплоснабжение приточных установок", 2)

    assert [entry["page"] for entry in ordered][0] == 2


def test_requirement_without_rooms_still_gets_sheets_and_vision(tmp_path):
    """Главное: дорогой шаг выполняется, а не отменяется отсутствием якоря."""
    path = _pdf(tmp_path / "рд.pdf", [
        "Общие данные",
        "План сетей теплоснабжения приточных установок",
    ])
    general = _req("Теплоснабжение приточных установок предусмотрено от узла.")
    seen = []

    def fake_vision(pdf_path, page_no, requirement_text, rooms, config, **kw):
        seen.append((Path(pdf_path).name, page_no, tuple(rooms)))
        return {"verdict": "confirmed", "reason": "показано на листе", "where": "лист 2"}

    result = check_compliance(
        [general],
        rd_text_facts=[{"page": 1, "text": "Общие данные"}],
        rd_sources=[(path, "рд.pdf")],
        config=object(),
        llm_verify=lambda *a, **kw: [
            {"sentence": general.sentence, "verdict": "absent", "reason": "нет в тексте"}],
        vision_check=fake_vision,
        max_visual_pages=2,
    )

    assert seen, "требование без номера помещения осталось без просмотра листа"
    assert result.items[0].status == STATUS_CONFIRMED
    assert result.diagnostics["vision_calls"] >= 1


def test_sheets_picked_by_text_are_marked_as_such(tmp_path):
    """Инспектор должен видеть, по чему ему подобрали лист (Г.106)."""
    path = _pdf(tmp_path / "рд.pdf", ["Общие данные", "План сетей"])
    general = _req("Теплоснабжение приточных установок предусмотрено от узла.")

    result = check_compliance(
        [general],
        rd_text_facts=[{"page": 1, "text": "Общие данные"}],
        rd_sources=[(path, "рд.pdf")],
        config=object(),
        llm_verify=lambda *a, **kw: [
            {"sentence": general.sentence, "verdict": "absent", "reason": "нет в тексте"}],
        vision_check=lambda *a, **kw: {"verdict": "unclear", "reason": "не разобрать"},
        max_visual_pages=2,
    )

    item = result.items[0]
    assert item.status == STATUS_NEEDS_CHECK
    assert item.pages_to_check, "листы для просмотра должны быть названы инспектору"
    assert "по близости текста листа" in item.detail, item.detail
