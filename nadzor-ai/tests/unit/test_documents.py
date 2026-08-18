"""Разбор документов и извлечение фактов с координатами."""
from __future__ import annotations

from pathlib import Path

from documents.extract import extract_facts, join_wrapped
from documents.pdf import parse
from documents.schemas import DocKind, StateKind
from documents.stamp import detect_kind, parse_stamp

ROOT = Path(__file__).resolve().parents[2]
PD3 = ROOT / "data/demo/generated/OBJ-001/pd_rev3/2024-15-KR_izm3.pdf"
AOSR = ROOT / "data/demo/generated/OBJ-001/as_built/aosr_47.pdf"


def test_document_kind_is_detected_by_content():
    """Вид документа определяется по заголовку, а не по имени файла."""
    assert detect_kind(parse(PD3.read_bytes())) == (DocKind.PD, StateKind.PD)
    assert detect_kind(parse(AOSR.read_bytes())) == (DocKind.AOSR, StateKind.AS_BUILT)


def test_stamp_is_parsed():
    stamp = parse_stamp(parse(PD3.read_bytes()))
    assert stamp.code == "2024-15-КР"
    assert stamp.revision == "3"
    assert stamp.stage == "П"
    assert stamp.doc_date.isoformat() == "2024-06-26"
    assert stamp.confidence >= 0.8


def test_every_fact_has_source_reference():
    """Факт без координат в систему не попадает."""
    facts = extract_facts(parse(AOSR.read_bytes()), "D1", "F1")
    assert facts
    for fact in facts:
        assert fact.source.page >= 1
        x0, y0, x1, y1 = fact.source.bbox
        assert x1 > x0 and y1 > y0


def test_specification_rows_are_extracted():
    facts = extract_facts(parse(PD3.read_bytes()), "D1", "F1")
    elements = {f.key: f.value for f in facts if f.fact_type == "element"}
    assert elements["ПП-8"]["concrete_class"] == "B25"
    assert elements["ПП-8"]["size"] == "180 мм"
    assert elements["К-2"]["rebar"] == "A400"


def test_wrapped_cells_are_joined_correctly():
    assert join_wrapped("+3.000…+75.00\n0") == "+3.000…+75.000"
    assert join_wrapped("Многоквартирный жилой дом\nс автостоянкой") == \
        "Многоквартирный жилой дом с автостоянкой"
