import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.coord_registry import (
    coordinate_match_ratio,
    shares_coordinate_system,
    transfer_anchor_bbox,
)


def _page_with_labels(labels: dict[str, tuple[float, float]]):
    doc = pymupdf.open()
    page = doc.new_page(width=2000, height=2000)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    for text, (x, y) in labels.items():
        page.insert_text((x, y), text, fontname="F0", fontsize=12)
    return doc, page


def test_identical_coordinates_detected_as_shared():
    """Реальное наблюдение с комплекта (Г.29): один и тот же номер
    помещения на разных стадиях документа встречается в одних и тех же
    координатах — общая CAD-подложка."""
    labels = {"140": (500.0, 600.0), "142": (700.0, 600.0), "147": (900.0, 600.0)}
    doc_a, page_a = _page_with_labels(labels)
    doc_b, page_b = _page_with_labels(labels)
    try:
        assert shares_coordinate_system(page_a, page_b, {"140", "142", "147"})
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: совпадающие координаты якорей распознаются как общая подложка")


def test_different_coordinates_not_shared():
    doc_a, page_a = _page_with_labels({"140": (500.0, 600.0), "142": (700.0, 600.0), "147": (900.0, 600.0)})
    doc_b, page_b = _page_with_labels({"140": (100.0, 100.0), "142": (300.0, 100.0), "147": (1500.0, 1500.0)})
    try:
        assert not shares_coordinate_system(page_a, page_b, {"140", "142", "147"})
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: разные координаты якорей не считаются общей подложкой")


def test_too_few_shared_keys_is_not_enough_evidence():
    """Одного совпадения недостаточно для вывода (Г.11 — не выводить
    правило по единственному наблюдению): min_keys держит планку."""
    doc_a, page_a = _page_with_labels({"140": (500.0, 600.0)})
    doc_b, page_b = _page_with_labels({"140": (500.0, 600.0)})
    try:
        assert not shares_coordinate_system(page_a, page_b, {"140"}, min_keys=3)
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: одного совпавшего якоря недостаточно, чтобы считать систему общей")


def test_partial_match_below_ratio_rejected():
    """Часть якорей совпала, часть — нет (доля ниже порога) — общая
    подложка не подтверждается, перенос координат не разрешается."""
    doc_a, page_a = _page_with_labels({"140": (500.0, 600.0), "142": (700.0, 600.0), "147": (900.0, 600.0), "198": (1100.0, 600.0)})
    doc_b, page_b = _page_with_labels({"140": (500.0, 600.0), "142": (700.0, 600.0), "147": (200.0, 200.0), "198": (1600.0, 1600.0)})
    try:
        ratio, total = coordinate_match_ratio(page_a, page_b, {"140", "142", "147", "198"})
        assert total == 4, total
        assert ratio == 0.5, ratio
        assert not shares_coordinate_system(page_a, page_b, {"140", "142", "147", "198"}, min_ratio=0.8)
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: доля совпадений ниже порога не даёт ложного подтверждения общей подложки")


def test_transfer_anchor_bbox_only_when_confirmed():
    labels_shared = {"140": (500.0, 600.0), "142": (700.0, 600.0), "147": (900.0, 600.0)}
    doc_a, page_a = _page_with_labels({**labels_shared, "012": (50.0, 50.0)})
    doc_b, page_b = _page_with_labels(labels_shared)
    try:
        # общая подложка подтверждена по независимым ключам -> "012" переносится
        transferred = transfer_anchor_bbox(page_a, page_b, "012", {"140", "142", "147"})
        assert len(transferred) == 1, transferred

        # без подтверждения (те же три ключа, но с другими координатами на B) — перенос запрещён
        doc_c, page_c = _page_with_labels({"140": (10.0, 10.0), "142": (20.0, 10.0), "147": (30.0, 10.0)})
        try:
            rejected = transfer_anchor_bbox(page_a, page_c, "012", {"140", "142", "147"})
            assert rejected == [], rejected
        finally:
            doc_c.close()
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: bbox переносится только при подтверждённой общей системе координат, иначе пустой список")


def test_key_missing_on_one_side_excluded_from_denominator():
    doc_a, page_a = _page_with_labels({"140": (500.0, 600.0), "142": (700.0, 600.0)})
    doc_b, page_b = _page_with_labels({"140": (500.0, 600.0)})
    try:
        ratio, total = coordinate_match_ratio(page_a, page_b, {"140", "142", "999"})
        assert total == 1, total  # только "140" есть на обеих сторонах
        assert ratio == 1.0, ratio
    finally:
        doc_a.close()
        doc_b.close()
    print("OK: ключ, отсутствующий на одной из сторон, не портит знаменатель доли")


if __name__ == "__main__":
    test_identical_coordinates_detected_as_shared()
    test_different_coordinates_not_shared()
    test_too_few_shared_keys_is_not_enough_evidence()
    test_partial_match_below_ratio_rejected()
    test_transfer_anchor_bbox_only_when_confirmed()
    test_key_missing_on_one_side_excluded_from_denominator()
    print("ALL PASS")
