import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import dimension_vision
from app.dimension_vision import read_dimensions_vision, render_anchor_crop_png


def _synthetic_page():
    doc = pymupdf.open()
    page = doc.new_page(width=1000, height=1000)
    font = pymupdf.Font(fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    page.insert_font(fontname="F0", fontbuffer=font.buffer)
    page.insert_text((500, 500), "А", fontname="F0", fontsize=14)
    return doc, page


def _patch_call_llm_json(fake_fn):
    original = dimension_vision.call_llm_json
    dimension_vision.call_llm_json = fake_fn
    return original


def test_render_anchor_crop_png_produces_real_image():
    doc, page = _synthetic_page()
    try:
        png = render_anchor_crop_png(page, (495.0, 495.0, 510.0, 512.0))
        assert png.startswith(b"\x89PNG")
        assert len(png) > 500
    finally:
        doc.close()
    print("OK: рендер зоны вокруг произвольного якоря (не помещения) даёт настоящий PNG")


def test_read_dimensions_vision_parses_multiple_values():
    doc, page = _synthetic_page()
    try:
        bbox = (495.0, 495.0, 510.0, 512.0)

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            assert images and len(images) == 1
            assert "А" in user_text
            return {"found": True, "dimensions": [
                {"value": "800x400", "kind": "section"},
                {"value": "Nэл.=34,0", "kind": "power"},
            ]}

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            result = read_dimensions_vision(page, bbox, "А", config=None)
        finally:
            dimension_vision.call_llm_json = original

        assert result["anchor"] == "А"
        assert result["found"] is True
        values = {d["value"] for d in result["dimensions"]}
        assert values == {"800x400", "Nэл.=34,0"}, values
    finally:
        doc.close()
    print("OK: несколько параметров у одного якоря разбираются в список фактов")


def test_read_dimensions_vision_honest_empty():
    doc, page = _synthetic_page()
    try:
        bbox = (495.0, 495.0, 510.0, 512.0)

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            return {"found": False, "dimensions": []}

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            result = read_dimensions_vision(page, bbox, "А", config=None)
        finally:
            dimension_vision.call_llm_json = original

        assert result["found"] is False
        assert result["dimensions"] == []
    finally:
        doc.close()
    print("OK: честное «рядом ничего нет» не подменяется выдумкой")


def test_read_dimensions_vision_ignores_malformed_items():
    doc, page = _synthetic_page()
    try:
        bbox = (495.0, 495.0, 510.0, 512.0)

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            return {"found": True, "dimensions": [
                {"value": "800x400", "kind": "section"},
                {"value": ""},           # пустое значение — отбрасывается
                "не словарь",            # не dict — отбрасывается
                {"kind": "section"},     # нет value — отбрасывается
            ]}

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            result = read_dimensions_vision(page, bbox, "А", config=None)
        finally:
            dimension_vision.call_llm_json = original

        assert len(result["dimensions"]) == 1, result
        assert result["dimensions"][0]["value"] == "800x400"
    finally:
        doc.close()
    print("OK: некорректные элементы ответа модели отбрасываются, не роняют разбор")


def test_read_dimensions_vision_none_when_model_fails():
    doc, page = _synthetic_page()
    try:
        bbox = (495.0, 495.0, 510.0, 512.0)

        def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
            return None

        original = _patch_call_llm_json(fake_call_llm_json)
        try:
            result = read_dimensions_vision(page, bbox, "А", config=None)
        finally:
            dimension_vision.call_llm_json = original

        assert result is None
    finally:
        doc.close()
    print("OK: сорванный вызов модели даёт None")


if __name__ == "__main__":
    test_render_anchor_crop_png_produces_real_image()
    test_read_dimensions_vision_parses_multiple_values()
    test_read_dimensions_vision_honest_empty()
    test_read_dimensions_vision_ignores_malformed_items()
    test_read_dimensions_vision_none_when_model_fails()
    print("ALL PASS")
