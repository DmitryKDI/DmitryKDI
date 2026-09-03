import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classification import classify_document, open_pdf, scan_text_for_discipline_codes

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)


def test_filename_signal_matches_js():
    cases = {
        "V2_01-05-04-02-07_Том 5.4.2 ОВ (1).pdf": "ОВ",
        "V2_01-05-04-01-09_Том 5.4.1.pdf": None,
        "АОСР №1-ОВ2.1 от 20.12.2024 Отопление.pdf": "ОВ",
        "АОСР №1_ОВ. от 07.04.2025.pdf": "ОВ",
        "просто обычный текст без кода.pdf": None,
    }
    for name, expect in cases.items():
        codes = scan_text_for_discipline_codes(name)
        found = None
        counts: dict[str, int] = {}
        for c in codes:
            counts[c] = counts.get(c, 0) + 3
        if counts:
            found = max(counts.items(), key=lambda kv: kv[1])
            found = found[0] if found[1] >= 3 else None
        assert found == expect, f"{name}: got {found}, expected {expect}"
    print("OK: filename signal matches JS logic")


def test_scan_finds_codes_added_from_real_composition_registry():
    """Г.63 — реальные шифры из «Состава документации» этого объекта
    (АНО/150321/1-РД-ОВ1, стр.10-12), найденные не текстовым угадыванием, а
    самим `composition_registry.py` (Г.62): наружные/внутренние сети,
    тепловой пункт, слаботочка, вертикальный транспорт — раньше не
    входили в DISCIPLINE_CODES вообще."""
    cases = {
        "АНО/150321/1-РД-ВВ": "ВВ",
        "АНО/150321/1-РД-ИТП.УУТЭ": "ИТП",
        "АНО/150321/1-РД-СКУД": "СКУД",
        "АНО/150321/1-РД-ВТ": "ВТ",
        "АНО/150321/1-РД-АУПТ": "АУПТ",
    }
    for text, expect in cases.items():
        codes = scan_text_for_discipline_codes(text)
        assert expect in codes, f"{text}: {codes} не содержит {expect}"
    print("OK: реальные коды разделов из Состава документации распознаются")


def test_real_document_stamp_is_image_not_text():
    """Подтверждает находку сессии: штамп rd_floor1.pdf — растровая картинка,
    без текстового слоя штампа, поэтому чисто текстовая классификация не
    находит код по штампу (только заголовок + имя файла)."""
    pdf_path = SAMPLE_DIR / "rd_floor1.pdf"
    result = classify_document(str(pdf_path), "rd_floor1.pdf")
    # ASCII-имя без кода, страница без текстового штампа -> код не найден без vision
    assert result.discipline_code is None, result
    assert result.source == "none"
    print("OK: real as-built stamp is image-only, text-only classification correctly returns None")


def test_vision_fallback_reads_real_stamp_code():
    """Если подключить vision-функцию, код должен читаться из картинки штампа
    — на реальном листе там написано «АНО/150321/1-РД-ОВ1»."""
    pdf_path = SAMPLE_DIR / "rd_floor1.pdf"

    def fake_vision_stamp_fn(png_bytes: bytes):
        assert len(png_bytes) > 1000  # реальная картинка, не пустышка
        return "ОВ"  # то, что модель реально прочитала бы с этого штампа

    result = classify_document(str(pdf_path), "rd_floor1.pdf", vision_stamp_fn=fake_vision_stamp_fn)
    assert result.discipline_code == "ОВ", result
    assert result.source == "stamp_vision"
    assert result.used_vision is True
    print("OK: vision fallback correctly classifies image-only stamp")


def test_title_page_signal():
    """Синтетический случай: титульный лист (без ключевых слов штампа) с
    шифром прямым текстом должен сработать без похода в штамп/vision."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    page.insert_text((72, 72), "Общество с ограниченной ответственностью", fontname="F0")
    page.insert_text((72, 100), "Шифр проекта: AI-15-2023-АР", fontname="F0")
    page.insert_text((72, 130), "Раздел 3. Архитектурные решения", fontname="F0")
    tmp_path = "/tmp/synthetic_title_page.pdf"
    doc.save(tmp_path)
    doc.close()

    result = classify_document(tmp_path, "безымянный.pdf")
    assert result.discipline_code == "АР", result
    assert result.source == "title_page", result
    print("OK: title page text signal works without vision")


def test_filename_wins_before_opening_pdf_at_all():
    """Имя файла с кодом должно давать результат без сканирования штампа —
    проверяем через файл, у которого штамп заведомо не про этот раздел."""
    pdf_path = SAMPLE_DIR / "rd_floor1.pdf"
    result = classify_document(str(pdf_path), "Раздел АР план.pdf")
    assert result.discipline_code == "АР", result
    assert result.source == "filename", result
    print("OK: filename signal short-circuits before stamp scan")


def _make_pdf(path: Path, owner_pw: str = "", user_pw: str = "") -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "test page")
    if owner_pw or user_pw:
        doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw=owner_pw, user_pw=user_pw)
    else:
        doc.save(str(path))
    doc.close()


def test_open_pdf_unlocks_owner_password_only_file(tmp_path):
    """Реальный случай на боевых документах: PDF-экспорт из CAD с owner-
    паролем (только запрет копирования/печати, пароль на ОТКРЫТИЕ не задан).
    PyMuPDF отдаёт такой файл как открытый и без явной авторизации — здесь
    просто фиксируем, что open_pdf не ломает этот случай, который и так
    работал, чтобы не откатить его при следующей правке."""
    path = tmp_path / "owner_protected.pdf"
    _make_pdf(path, owner_pw="ownersecret", user_pw="")
    doc = open_pdf(str(path))
    try:
        assert doc.page_count == 1
        assert "test page" in doc[0].get_text()
    finally:
        doc.close()
    print("OK: файл с owner-паролем открывается и читается как обычно")


def test_open_pdf_raises_clear_error_for_user_password_file(tmp_path):
    """Файл, требующий пароль именно на открытие (needs_pass), — снять его
    пустым паролем нельзя; open_pdf должен упасть понятной ошибкой, а не
    отдать документ с недоступными страницами (пустой текст, 0 листов молча)."""
    path = tmp_path / "user_protected.pdf"
    _make_pdf(path, owner_pw="ownersecret", user_pw="realpassword")
    with pytest.raises(ValueError, match="паролем"):
        open_pdf(str(path))
    print("OK: PDF с паролем на открытие — понятная ошибка вместо тихого 0 страниц")


def test_open_pdf_plain_file_unaffected(tmp_path):
    path = tmp_path / "plain.pdf"
    _make_pdf(path)
    doc = open_pdf(str(path))
    try:
        assert doc.page_count == 1
    finally:
        doc.close()
    print("OK: обычный PDF без пароля открывается как раньше")


if __name__ == "__main__":
    test_filename_signal_matches_js()
    test_scan_finds_codes_added_from_real_composition_registry()
    test_real_document_stamp_is_image_not_text()
    test_vision_fallback_reads_real_stamp_code()
    test_title_page_signal()
    test_filename_wins_before_opening_pdf_at_all()
    print("ALL PASS")
