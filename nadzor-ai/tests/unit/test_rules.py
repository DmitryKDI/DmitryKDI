"""Детерминированный слой детектора."""
from __future__ import annotations

from analysis.rules.common import (
    concrete_class,
    deviation_percent,
    parse_amount,
    parse_geometry,
    rebar_class,
)


def test_cyrillic_and_latin_designations_are_equivalent():
    """В документах встречаются «В25» и «B25» — это одно и то же."""
    assert concrete_class("Бетонная смесь БСГ В25 П4") == "B25"
    assert concrete_class("класс бетона B25") == "B25"
    assert rebar_class("Арматура класса А500С") == "A500C"


def test_volume_parsing():
    assert parse_amount("142,0 м³") == (142.0, "м³")
    assert parse_geometry("площадь 658,0 м², толщина 180 мм") == \
        {"area_m2": 658.0, "thickness_mm": 180.0}
    assert deviation_percent(142.0, 118.44) == 19.9


def test_material_downgrade_is_found(demo_result):
    downgrades = [f for f in demo_result["findings"]
                  if f.code == "D-02" and f.transition == "T5"]
    assert downgrades, "снижение класса бетона в акте не найдено"
    finding = downgrades[0]
    assert finding.severity == "critical"
    assert finding.structural_element["mark"] == "СТ-1"
    assert len(finding.evidence) == 2
    assert {e.role for e in finding.evidence} == {"требование", "факт"}


def test_chronology_rule(demo_result):
    chrono = [f for f in demo_result["findings"] if f.code == "D-05"]
    assert chrono and "51" in chrono[0].title


def test_sro_rule_aggregates_acts(demo_result):
    """Все акты за пределами срока сводятся в одну находку."""
    sro = [f for f in demo_result["findings"] if f.code == "D-14"]
    assert len(sro) == 1
    assert "СРО" in sro[0].title


def test_rules_do_not_depend_on_llm(demo_result):
    """Даже при отказе LLM-слоя формальные нарушения находятся правилами."""
    rule_findings = [f for f in demo_result["findings"] if f.detector != "llm"]
    codes = {f.code for f in rule_findings}
    assert {"D-02", "D-05", "D-07", "D-11", "D-14", "D-90"} <= codes


def test_every_finding_has_norm_and_coordinates(demo_result):
    """Требование Б.6: вывод без пункта норматива и координат не сохраняется."""
    for finding in demo_result["findings"]:
        assert finding.norm_refs, f"{finding.public_id} без нормативного основания"
        assert finding.evidence, f"{finding.public_id} без доказательств"
        for ref in finding.norm_refs:
            assert ref["doc"] and ref["clause"]
            assert "actual" in ref
