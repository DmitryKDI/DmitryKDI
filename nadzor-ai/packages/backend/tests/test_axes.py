import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.classification import open_pdf
from app.axes import KIND_AXIS, KIND_ELEVATION, extract_axis_anchors, extract_elevation_anchors


def _page_with_words(words: list[tuple[str, float, float]], width=2000, height=2000):
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    for text, x, y in words:
        page.insert_text((x, y), text, fontname="F0", fontsize=12)
    return doc, page


def test_ruler_column_of_distinct_letters_is_detected():
    words = [(ch, 100.0, y) for ch, y in zip("АБВГДЕЖ", [100, 200, 300, 400, 500, 600, 700])]
    doc, page = _page_with_words(words)
    try:
        anchors = extract_axis_anchors(page)
        texts = {a.text for a in anchors}
        assert texts == set("АБВГДЕЖ"), texts
        assert all(a.kind == KIND_AXIS for a in anchors)
    finally:
        doc.close()
    print("OK: колонка разных осевых букв распознаётся как линейка")


def test_repeated_label_column_is_not_a_ruler():
    """Реальное наблюдение (Г.11): марка трубопровода («ВК»), повторённая
    много раз вдоль одной вертикали, по форме — тоже колонка коротких
    кириллических токенов, но это не осевая сетка."""
    words = [("ВК", 100.0, y) for y in (100, 200, 300, 400, 500, 600, 700, 800)]
    doc, page = _page_with_words(words)
    try:
        anchors = extract_axis_anchors(page)
        assert anchors == [], anchors
    finally:
        doc.close()
    print("OK: повторяющаяся марка трубопровода не принимается за осевую линейку")


def test_mixed_ruler_and_repeated_label_both_present():
    ruler = [(ch, 100.0, y) for ch, y in zip("АБВГД", [100, 200, 300, 400, 500])]
    noise = [("ВК", 900.0, y) for y in (100, 200, 300, 400, 500, 600)]
    doc, page = _page_with_words(ruler + noise)
    try:
        anchors = extract_axis_anchors(page)
        texts = {a.text for a in anchors}
        assert texts == set("АБВГД"), texts
    finally:
        doc.close()
    print("OK: настоящая линейка находится, соседний шум из марки трубопровода — нет")


def test_short_cluster_below_min_size_is_rejected():
    """Несколько случайных коротких токенов рядом друг с другом —
    недостаточно данных, чтобы утверждать «это линейка» (Г.11: реальный
    замер поймал именно такое 3-элементное случайное совпадение)."""
    words = [("А", 100.0, 100.0), ("Б", 100.0, 110.0), ("В", 100.0, 120.0), ("Г", 100.0, 130.0)]
    doc, page = _page_with_words(words)
    try:
        assert extract_axis_anchors(page) == []
    finally:
        doc.close()
    print("OK: кластер короче минимального размера не считается линейкой")


def test_gap_larger_than_threshold_splits_clusters():
    near = [(ch, 100.0, y) for ch, y in zip("АБВГД", [100, 110, 120, 130, 140])]
    far = [(ch, 100.0, y) for ch, y in zip("ЕЖЗИК", [900, 910, 920, 930, 940])]
    doc, page = _page_with_words(near + far)
    try:
        anchors = extract_axis_anchors(page)
        near_texts = {a.text for a in anchors if a.center[1] < 500}
        far_texts = {a.text for a in anchors if a.center[1] >= 500}
        assert near_texts == set("АБВГД"), near_texts
        assert far_texts == set("ЕЖЗИК"), far_texts
    finally:
        doc.close()
    print("OK: разрыв больше порога не склеивает два независимых кластера в один")


def test_no_candidates_returns_empty_not_crash():
    doc, page = _page_with_words([("Экспликация", 100.0, 100.0)])
    try:
        assert extract_axis_anchors(page) == []
    finally:
        doc.close()
    print("OK: лист без осеподобных токенов даёт пустой список, не падает")


def test_elevation_marks_extracted():
    words = [("±0.000", 100.0, 100.0), ("-0.014", 200.0, 200.0), ("+3.900", 300.0, 300.0)]
    doc, page = _page_with_words(words)
    try:
        anchors = extract_elevation_anchors(page)
        texts = {a.text for a in anchors}
        assert texts == {"±0.000", "-0.014", "+3.900"}, texts
        assert all(a.kind == KIND_ELEVATION for a in anchors)
    finally:
        doc.close()
    print("OK: отметки высот извлекаются без кластеризации")


def test_elevation_does_not_match_axis_token():
    doc, page = _page_with_words([("±0.000", 100.0, 100.0)])
    try:
        assert extract_axis_anchors(page) == []
    finally:
        doc.close()
    print("OK: отметка высоты не попадает в осевые якоря")


def test_real_sheet_ruler_found_and_pipe_markers_excluded():
    """Смоук на реальном листе (замер в докстринге модуля): колонка полной
    осевой линейки находится, повторяющиеся марки трубопроводов — нет.
    Не проверяем точное число (Г.24 — не зашивать числа, которые могут быть
    ответом слепого прогона), только структурные свойства."""
    doc = open_pdf("/home/user/nadzor_sample/АНО-150321-1-РД-ОВ1 изм. 4_в1 (1)-1-100.pdf")
    try:
        page = doc[17]
        anchors = extract_axis_anchors(page)
        assert len(anchors) > 10, "на реальном листе должна найтись развёрнутая линейка"
        texts = [a.text for a in anchors]
        assert texts.count("ВК") == 0, "марка трубопровода не должна попасть в якоря осей"
        elevations = extract_elevation_anchors(page)
        assert any(a.text == "±0.000" for a in elevations), elevations
    finally:
        doc.close()
    print("OK: на реальном листе линейка находится, марки трубопроводов исключены")


if __name__ == "__main__":
    test_ruler_column_of_distinct_letters_is_detected()
    test_repeated_label_column_is_not_a_ruler()
    test_mixed_ruler_and_repeated_label_both_present()
    test_short_cluster_below_min_size_is_rejected()
    test_gap_larger_than_threshold_splits_clusters()
    test_no_candidates_returns_empty_not_crash()
    test_elevation_marks_extracted()
    test_elevation_does_not_match_axis_token()
    test_real_sheet_ruler_found_and_pipe_markers_excluded()
    print("ALL PASS")
