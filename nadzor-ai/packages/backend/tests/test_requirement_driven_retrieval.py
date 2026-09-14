from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from deep_transfer_compare import _valid_explicit_evidence  # noqa: E402
from requirement_driven_retrieval import (  # noqa: E402
    REQUIREMENT_COMPARE_SYSTEM,
    normalize_requirements,
    requirement_batches,
    requirement_rd_context,
)


def test_normalize_requirements_keeps_exact_entity_ids():
    rows = normalize_requirements({
        "requirements": [{
            "id": "REQ-X",
            "pd_page": 2,
            "requirement": "Для помещения 901 систему EX-Z9 подключить к FAN-Z9.",
            "rooms": ["901"],
            "systems": ["EX-Z9"],
            "equipment": ["FAN-Z9"],
            "marks": ["EX-Z9", "FAN-Z9"],
        }]
    })
    assert rows[0]["rooms"] == ["901"]
    assert rows[0]["marks"] == ["EX-Z9", "FAN-Z9"]
    assert rows[0]["pd_page"] == 2


def test_exact_room_and_mark_beat_functionally_similar_page():
    requirements = normalize_requirements({
        "requirements": [{
            "id": "REQ-001",
            "pd_page": 1,
            "requirement": "В помещении 901 предусмотреть отдельную вытяжку EX-Z9.",
            "rooms": ["901"],
            "room_types": ["санузел"],
            "systems": ["EX-Z9"],
            "marks": ["EX-Z9"],
            "search_terms": ["вытяжка", "санузел"],
        }]
    })
    fake_path = Path("rd.pdf")

    def fake_extract(path: Path):
        assert path == fake_path
        return [
            {"page": 1, "text": "Санузел помещения 777. Предусмотрена вытяжка общего типа."},
            {"page": 2, "text": "Помещение 901. Система EX-Z9 показана на плане."},
        ]

    context, coverage = requirement_rd_context(
        requirements, [fake_path], fake_extract, per_requirement_pages=1
    )
    assert coverage[0]["candidate_pages"][0]["page"] == 2
    assert "page 2" in context
    assert "page 1" not in context


def test_short_noise_words_do_not_rank_pages():
    requirements = normalize_requirements({
        "requirements": [{
            "id": "REQ-001",
            "requirement": "Трубопроводы прокладывать под потолком при наличии учащихся.",
            "search_terms": ["под", "при", "учащиеся"],
        }]
    })
    fake_path = Path("rd.pdf")

    def fake_extract(path: Path):
        return [
            {"page": 1, "text": "Под столом при входе размещена посторонняя запись."},
            {"page": 2, "text": "В зоне учащихся трубопроводы отопления проложены скрыто."},
        ]

    _context, coverage = requirement_rd_context(requirements, [fake_path], fake_extract, per_requirement_pages=2)
    pages = coverage[0]["candidate_pages"]
    assert pages[0]["page"] == 2
    assert "под" not in pages[0]["strong_hits"]
    assert "при" not in pages[0]["strong_hits"]


def test_matched_requires_explicit_rd_evidence():
    system = REQUIREMENT_COMPARE_SYSTEM.casefold()
    assert "matched разрешен только при явном доказательстве" in system
    assert "подразумевается" in system
    assert _valid_explicit_evidence({"evidence": []}) is False
    assert _valid_explicit_evidence({
        "evidence": [{"document": "rd.pdf", "page": 2, "quote": "EX-Z9 показана на плане"}]
    }) is True


def test_requirement_batches_preserve_all_requirements():
    rows = [{"id": f"REQ-{i}"} for i in range(9)]
    batches = requirement_batches(rows, size=4)
    assert [len(x) for x in batches] == [4, 4, 1]
    assert [x["id"] for batch in batches for x in batch] == [x["id"] for x in rows]
