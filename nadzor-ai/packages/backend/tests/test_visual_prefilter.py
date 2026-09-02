import sys
import tempfile
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.visual_prefilter import diff_hot_zone, is_visually_different, visual_diff_ratio


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


def test_diff_hot_zone_none_when_identical():
    path = _pdf_with_rect((50, 50, 150, 150))
    assert diff_hot_zone(path, 1, path, 1) is None
    print("OK: одинаковый лист сам с собой — зоны отличий нет")


def test_diff_hot_zone_none_when_diff_covers_whole_page():
    """Г.55: отличия разбросаны по всему листу — локализовывать нечего,
    кроп такой ширины не сузил бы картинку модели."""
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    assert diff_hot_zone(blank, 1, filled, 1) is None
    print("OK: отличие на весь лист не даёт зоны — сравнивается лист целиком, как раньше")


def test_diff_hot_zone_localizes_small_corner_change():
    """Реальный сценарий Г.55: насыщенный план в целом одинаков, но в одном
    углу (аналог локального изменения воздуховодной обвязки) появился
    новый элемент — зона должна охватить именно этот угол, не весь лист."""
    same_base = (50, 50, 100, 100)
    before = _pdf_with_rect(same_base)
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    page.draw_rect(pymupdf.Rect(*same_base), color=(0, 0, 0), fill=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(320, 320, 380, 380), color=(0, 0, 0), fill=(0, 0, 0))
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    after = tmp.name

    zone = diff_hot_zone(before, 1, after, 1)
    assert zone is not None, "локальное изменение в углу должно дать зону"
    x0, y0, x1, y1 = zone
    # зона должна лежать в правом нижнем углу (320-380 из 400 = 0.8-0.95),
    # не покрывать весь лист
    assert x0 > 0.5 and y0 > 0.5, zone
    assert (x1 - x0) < 0.6 and (y1 - y0) < 0.6, zone
    print("OK: локальное изменение в углу листа даёт локальную зону, не весь лист")


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
    test_diff_hot_zone_none_when_identical()
    test_diff_hot_zone_none_when_diff_covers_whole_page()
    test_diff_hot_zone_localizes_small_corner_change()
    test_is_visually_different_false_on_render_failure_not_crash()
    print("ALL PASS")
