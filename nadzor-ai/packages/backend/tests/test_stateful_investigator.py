from __future__ import annotations

from pathlib import Path

from app.inspector_memory import active_lessons, add_lesson
import pytest
from app.matching import DocumentInput
from app.stateful_investigator import (
    _normalize_candidate,
    _search,
    build_page_catalog,
)


def _doc(name: str, side_text: str) -> DocumentInput:
    return DocumentInput(
        name=name,
        pages=2,
        text_facts=[
            {"page": 1, "text": side_text + " помещение 101 установка X"},
            {"page": 2, "text": side_text + " схема вентиляции Y"},
        ],
        room_facts=[
            {"page": 1, "key": "101", "name": "техпомещение"},
        ],
        equipment_facts=[
            {"page": 1, "key": "X", "name": "установка"},
        ],
        page_kinds={1: "drawing", 2: "drawing"},
        discipline_code="ОВ",
    )


def test_catalog_keeps_every_page_and_search_only_ranks():
    catalog = build_page_catalog([_doc("pd", "ПД")], [_doc("rd", "РД")])
    assert [row["ref"] for row in catalog] == ["PD0:P1", "PD0:P2", "RD0:P1", "RD0:P2"]
    matches = _search(catalog, "помещение 101 установка")
    assert matches
    assert {row["side"] for row in catalog} == {"PD", "RD"}


def test_candidate_requires_direct_pd_and_rd_observations():
    ref_map = {
        "PD0:P1": {"side": "PD"},
        "RD0:P1": {"side": "RD"},
    }
    assert _normalize_candidate(
        {
            "difference": "A != B",
            "pd_refs": ["PD0:P1"],
            "rd_refs": ["RD0:P1"],
            "pd_observation": "",
            "rd_observation": "B",
        },
        ref_map,
    ) is None
    candidate = _normalize_candidate(
        {
            "difference": "A != B",
            "difference_kind": "configuration",
            "pd_refs": ["PD0:P1"],
            "rd_refs": ["RD0:P1"],
            "pd_observation": "A",
            "rd_observation": "B",
            "requirement_ids": ["R1"],
        },
        ref_map,
    )
    assert candidate is not None
    assert candidate["difference_kind"] == "configuration"
    assert candidate["requirement_ids"] == ["R1"]


def test_memory_is_disabled_for_blind_benchmark(monkeypatch, tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    add_lesson("Проверяй все зоны многозонного требования.", path=path)
    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "0")
    assert len(active_lessons(path=path)) == 1
    monkeypatch.setenv("NADZOR_BLIND_BENCHMARK", "1")
    assert active_lessons(path=path) == []


def test_memory_rejects_object_specific_lesson(tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    with pytest.raises(ValueError):
        add_lesson("На листе 21 проверь помещение 267.", path=path)
