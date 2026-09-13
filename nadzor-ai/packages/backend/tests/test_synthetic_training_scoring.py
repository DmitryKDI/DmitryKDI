from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from train_synthetic_curriculum import _is_hit  # noqa: E402


def test_scorer_uses_concrete_mark_not_broad_discipline():
    result = {
        "suspicions": [
            {
                "rooms": ["528"],
                "systems": ["FAN-77"],
                "difference_type": "quantity",
                "confidence": 1.0,
                "title": "Изменение количества вентиляторов FAN-77",
            }
        ]
    }
    expected = {
        "room": "528",
        "mark": "FAN-77",
        "system": "VENT",
        "type": "quantity",
    }
    assert _is_hit(result, expected, True)[0] is True


def test_scorer_accepts_entity_match_when_llm_labels_connection_as_type_change():
    result = {
        "suspicions": [
            {
                "rooms": ["536"],
                "systems": ["H-85"],
                "difference_type": "type_change",
                "confidence": 1.0,
                "title": "Перестановка подачи и обратки H-85",
            }
        ]
    }
    expected = {
        "room": "536",
        "mark": "H-85",
        "system": "HEAT",
        "type": "connection",
    }
    hit, reason = _is_hit(result, expected, True)
    assert hit is True
    assert reason == "matched_room_entity"


def test_negative_control_still_rejects_material_false_positive():
    clean = {"suspicions": []}
    assert _is_hit(clean, None, False) == (True, "negative_control_ok")

    false_positive = {"suspicions": [{"confidence": 0.8, "title": "лишнее подозрение"}]}
    assert _is_hit(false_positive, None, False) == (False, "negative_control_false_positive")
