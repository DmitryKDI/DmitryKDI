import sys
import tempfile
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.visual_prefilter import is_visually_different, visual_diff_ratio


def _pdf_blank(width=400, height=400) -> str:
    doc = pymupdf.open()
    doc.new_page(width=width, height=height)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    return tmp.name


def _pdf_with_rect(rect_coords, width=400, height=400) -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(pymupdf.Rect(*rect_coords), color=(0, 0, 0), fill=(0, 0, 0))
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    return tmp.name


def test_identical_pages_have_low_diff_ratio():
    path = _pdf_with_rect((50, 50, 150, 150))
    ratio = visual_diff_ratio(path, 1, path, 1)
    assert ratio == 0.0, ratio
    print("OK: один и тот же лист сам с собой — нулевая разница")


def test_blank_vs_large_black_rect_has_high_diff_ratio():
    """Реальный сценарий Г.54: лист с воздуховодной обвязкой (много графики)
    против почти пустого — регистры совпадают по тексту, но лист другой."""
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    ratio = visual_diff_ratio(blank, 1, filled, 1)
    assert ratio > 0.5, ratio
    print("OK: пустой лист против закрашенного — высокая доля отличий")


def test_is_visually_different_true_above_threshold():
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    assert is_visually_different(blank, 1, filled, 1) is True
    print("OK: явно другой лист помечен как визуально отличающийся")


def test_is_visually_different_false_for_identical_pages():
    path = _pdf_with_rect((50, 50, 150, 150))
    assert is_visually_different(path, 1, path, 1) is False
    print("OK: одинаковый лист не считается визуально отличающимся")


def test_is_visually_different_false_on_render_failure_not_crash():
    """Битый путь/несуществующая страница — не расхождение, а сбой рендера;
    предфильтр не имеет права молча поднять бюджет там, где сам рендер не
    удался (это отдельная, уже видимая проблема выше по цепочке)."""
    path = _pdf_blank()
    assert is_visually_different("/no/such/file.pdf", 1, path, 1) is False
    print("OK: сбой рендера не считается визуальным расхождением, не роняет вызов")


if __name__ == "__main__":
    test_identical_pages_have_low_diff_ratio()
    test_blank_vs_large_black_rect_has_high_diff_ratio()
    test_is_visually_different_true_above_threshold()
    test_is_visually_different_false_for_identical_pages()
    test_is_visually_different_false_on_render_failure_not_crash()
    print("ALL PASS")
