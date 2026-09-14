import sys
import tempfile
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.visual_prefilter import (
    diff_hot_zone,
    is_visually_different,
    visual_change_evidence,
    visual_diff_ratio,
)


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


def _plan_pdf(*, shift_x=0, shift_y=0, extra=False) -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    shape = page.new_shape()
    for x in (90, 210, 330, 450):
        shape.draw_line((x + shift_x, 70 + shift_y), (x + shift_x, 320 + shift_y))
    for y in (100, 190, 280):
        shape.draw_line((70 + shift_x, y + shift_y), (500 + shift_x, y + shift_y))
    shape.draw_line((120 + shift_x, 140 + shift_y), (390 + shift_x, 240 + shift_y))
    if extra:
        shape.draw_line((250 + shift_x, 205 + shift_y), (315 + shift_x, 205 + shift_y))
        shape.draw_line((315 + shift_x, 205 + shift_y), (315 + shift_x, 250 + shift_y))
    shape.finish(width=2)
    shape.commit()
    page.insert_text((35, 25), "SYNTHETIC DRAWING", fontsize=10)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    return tmp.name


def test_identical_pages_have_low_diff_ratio():
    path = _pdf_with_rect((50, 50, 150, 150))
    ratio = visual_diff_ratio(path, 1, path, 1)
    assert ratio == 0.0, ratio


def test_blank_vs_large_black_rect_has_high_diff_ratio():
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    ratio = visual_diff_ratio(blank, 1, filled, 1)
    assert ratio > 0.5, ratio


def test_is_visually_different_true_above_threshold():
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    assert is_visually_different(blank, 1, filled, 1) is True


def test_is_visually_different_false_for_identical_pages():
    path = _pdf_with_rect((50, 50, 150, 150))
    assert is_visually_different(path, 1, path, 1) is False


def test_diff_hot_zone_none_when_identical():
    path = _pdf_with_rect((50, 50, 150, 150))
    assert diff_hot_zone(path, 1, path, 1) is None


def test_diff_hot_zone_none_when_diff_covers_whole_page():
    blank = _pdf_blank()
    filled = _pdf_with_rect((0, 0, 400, 400))
    assert diff_hot_zone(blank, 1, filled, 1) is None


def test_diff_hot_zone_localizes_small_corner_change():
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
    assert zone is not None
    x0, y0, x1, y1 = zone
    assert x0 > 0.5 and y0 > 0.5, zone
    assert (x1 - x0) < 0.6 and (y1 - y0) < 0.6, zone


def test_small_translation_is_registered_before_diff():
    before = _plan_pdf()
    after = _plan_pdf(shift_x=18, shift_y=10)
    evidence = visual_change_evidence(before, 1, after, 1)

    assert evidence["raw_diff_ratio"] >= evidence["diff_ratio"]
    assert evidence["alignment"]["overlap_ratio"] > 0.7
    assert abs(evidence["alignment"]["dx_cells"]) <= 3
    assert abs(evidence["alignment"]["dy_cells"]) <= 3


def test_local_change_survives_registration():
    before = _plan_pdf()
    after = _plan_pdf(shift_x=16, shift_y=8, extra=True)
    evidence = visual_change_evidence(before, 1, after, 1)

    assert evidence["changed_cells"] > 0
    assert evidence["diff_ratio"] > 0
    assert evidence["alignment"]["overlap_ratio"] > 0.7


def test_is_visually_different_false_on_render_failure_not_crash():
    path = _pdf_blank()
    assert is_visually_different("/no/such/file.pdf", 1, path, 1) is False
