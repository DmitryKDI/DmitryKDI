"""Нормативная база, актуальность редакций и приоритизация."""
from __future__ import annotations

import yaml
from pathlib import Path

from analysis import scoring

ROOT = Path(__file__).resolve().parents[2]
SCORING = yaml.safe_load((ROOT / "config/scoring.yaml").read_text(encoding="utf-8"))


def test_clause_lookup_and_search(norms):
    clause = norms.get("СП 63.13330.2018:6.1.6")
    assert clause and clause.is_actual
    results = norms.search("класс бетона ниже проектного", top_k=3)
    assert results[0].ref == "СП 63.13330.2018:6.1.6"


def test_superseded_edition_is_flagged(norms):
    check = norms.check_edition("СП 63.13330.2012:6.1.6")
    assert check["actual"] is False
    assert check["superseded_by"] == "СП 63.13330.2018:6.1.6"


def test_unknown_clause_is_reported(norms):
    check = norms.check_edition("СП 999.99999.2030:1.1")
    assert check["found"] is False


def test_priority_reflects_severity_and_structure():
    critical_slab = scoring.score("critical", 0.95, {"name": "Плита перекрытия"},
                                  [{"article": "9.4"}], "бетонирование", SCORING)
    minor_finish = scoring.score("minor", 0.6, {"name": "Отделка"},
                                 [{"article": "9.5"}], "отделка", SCORING)
    assert critical_slab > minor_finish
    assert 0 <= minor_finish <= critical_slab <= 100


def test_scoring_is_explainable():
    explained = scoring.explain("critical", 0.9, {"name": "Колонна"}, [{"article": "9.4"}],
                                "бетонирование колонн", SCORING)
    assert explained["total"] > 0
    assert set(explained["weights"]) >= {"severity", "confidence", "structural_criticality"}
