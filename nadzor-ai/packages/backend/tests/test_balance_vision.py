import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import balance_vision
from app.balance_vision import find_room_label_bboxes, read_balance_box_vision, render_balance_crop_png


def _synthetic_page_with_room_label():
    doc = pymupdf.open()
    page = doc.new_page(width=1000, height=1000)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    page.insert_text((500, 500), "140", fontname="F0", fontsize=14)
    return doc, page


def test_find_room_label_bboxes_locates_standalone_number():
    doc, page = _synthetic_page_with_room_label()
    try:
        bboxes = find_room_label_bboxes(page, "140")
        assert len(bboxes) == 1, bboxes
        assert bboxes[0][0] < bboxes[0][2] and bboxes[0][1] < bboxes[0][3], bboxes
    finally:
        doc.close()
    print("OK: номер помещения на плане находится как отдельное слово")


def test_find_room_label_bboxes_empty_for_missing_key():
    doc, page = _synthetic_page_with_room_label()
    try:
        assert find_room_label_bboxes(page, "999") == []
    finally:
        doc.close()
    print("OK: отсутствующий на листе номер не даёт ложных совпадений")


def test_render_balance_crop_png_produces_real_image():
    doc, page = _synthetic_page_with_room_label()
    try:
        bbox = find_room_label_bboxes(page, "140")[0]
        png = render_balance_crop_png(page, bbox)
        assert png.startswith(b"\x89PNG"), "не PNG"
        assert len(png) > 500, "подозрительно маленький рендер"
    finally:
        doc.close()
    print("OK: рендер зоны вокруг номера помещения даёт настоящий PNG")


def _patch_call_llm_json(fake_fn):
    original = balance_vision.call_llm_json
    balance_vision.call_llm_json = fake_fn
    return original


def test_read_balance_box_vision_parses_found_result():
    doc, page = _synthetic_page_with_room_label()
    try:
        bbox = find_room_label_bboxes(page, "140")[0]

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            assert images and len(images) == 1
            assert "140" in user_text
            return {"system_code": "П2/ВЕ", "pritok_m3ch": 400, "vytyazhka_m3ch": 400, "found": True}

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            fact = read_balance_box_vision(page, bbox, "140", config=None)
        finally:
            balance_vision.call_llm_json = original

        assert fact["room_key"] == "140", fact
        assert fact["found"] is True, fact
        assert fact["system_code"] == "П2/ВЕ", fact
        assert fact["приток_м3ч"] == "400", fact
        assert fact["вытяжка_м3ч"] == "400", fact
    finally:
        doc.close()
    print("OK: результат модели с найденной рамкой разбирается в структурированный факт")


def test_read_balance_box_vision_honest_not_found():
    """Модель честно сообщает, что рамки на кропе нет — пустой результат
    возвращается как есть, не выдумывается (раздел 0, п.7)."""
    doc, page = _synthetic_page_with_room_label()
    try:
        bbox = find_room_label_bboxes(page, "140")[0]

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            return {"system_code": None, "pritok_m3ch": None, "vytyazhka_m3ch": None, "found": False}

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            fact = read_balance_box_vision(page, bbox, "140", config=None)
        finally:
            balance_vision.call_llm_json = original

        assert fact["found"] is False, fact
        assert "приток_м3ч" not in fact, fact
        assert "вытяжка_м3ч" not in fact, fact
    finally:
        doc.close()
    print("OK: честный «рамки нет» не подменяется выдуманными числами")


def test_read_balance_box_vision_none_when_model_fails():
    doc, page = _synthetic_page_with_room_label()
    try:
        bbox = find_room_label_bboxes(page, "140")[0]

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            return None

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            fact = read_balance_box_vision(page, bbox, "140", config=None)
        finally:
            balance_vision.call_llm_json = original

        assert fact is None, fact
    finally:
        doc.close()
    print("OK: сорванный вызов модели даёт None, а не пустую выдумку")


if __name__ == "__main__":
    test_find_room_label_bboxes_locates_standalone_number()
    test_find_room_label_bboxes_empty_for_missing_key()
    test_render_balance_crop_png_produces_real_image()
    test_read_balance_box_vision_parses_found_result()
    test_read_balance_box_vision_honest_not_found()
    test_read_balance_box_vision_none_when_model_fails()
    print("ALL PASS")
