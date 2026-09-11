import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput
from app.room_cross_check import cross_check_rooms
from app.equip_cross_check import cross_check_equipment
from app.requirement_cross_check import cross_check_requirements
from app.requirement_registry import Requirement
from app.triangulation import (
    CANDIDATE,
    CONFIRMED,
    Confirmation,
    Signal,
    candidates_only,
    confirmed_only,
    signal_from_vision_verdict,
    signals_from_equip_cross_check,
    signals_from_requirement_cross_check,
    signals_from_room_cross_check,
    signals_from_routing_diff,
    signals_from_visual_requirement_checks,
    triangulate,
)


def test_two_independent_sources_confirm():
    signals = [
        Signal(source="text", domain="room", key="140", detail="а"),
        Signal(source="schema", domain="room", key="140", detail="б"),
    ]
    result = triangulate(signals)
    assert len(result) == 1, result
    assert result[0].status == CONFIRMED, result
    assert result[0].sources == ("schema", "text"), result


def test_single_source_is_only_candidate():
    result = triangulate([Signal(source="text", domain="room", key="147")])
    assert result[0].status == CANDIDATE


def test_repeated_hits_from_same_source_do_not_double_count():
    signals = [
        Signal(source="schema", domain="room", key="012", detail="наблюдение 1"),
        Signal(source="schema", domain="room", key="012", detail="наблюдение 2"),
    ]
    result = triangulate(signals)
    assert result[0].status == CANDIDATE
    assert result[0].sources == ("schema",)


def test_room_and_equipment_domains_do_not_collide():
    signals = [
        Signal(source="room_registry", domain="room", key="012"),
        Signal(source="equip_registry", domain="equipment", key="012"),
    ]
    result = triangulate(signals)
    assert len(result) == 2
    statuses = {(c.domain, c.key): c.status for c in result}
    assert statuses[("room", "012")] == CANDIDATE
    assert statuses[("equipment", "012")] == CANDIDATE


def test_min_sources_threshold_is_configurable():
    signals = [
        Signal(source="a", domain="room", key="1"),
        Signal(source="b", domain="room", key="1"),
        Signal(source="c", domain="room", key="1"),
    ]
    assert triangulate(signals, min_sources=4)[0].status == CANDIDATE
    assert triangulate(signals, min_sources=3)[0].status == CONFIRMED
    assert triangulate(signals, min_sources=2)[0].status == CONFIRMED


def test_confirmed_only_and_candidates_only_filters():
    confirmations = [
        Confirmation(domain="room", key="1", status=CONFIRMED, sources=("a", "b")),
        Confirmation(domain="room", key="2", status=CANDIDATE, sources=("a",)),
    ]
    assert [c.key for c in confirmed_only(confirmations)] == ["1"]
    assert [c.key for c in candidates_only(confirmations)] == ["2"]


def rf(page, key, name, area=None):
    fact = {"page": page, "key": key, "name": name}
    if area:
        fact["area"] = area
    return fact


def ef(page, key, name, qty=None):
    fact = {"page": page, "key": key, "name": name}
    if qty:
        fact["qty"] = qty
    return fact


def test_adapter_from_room_cross_check_feeds_strong_signal():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "013", "Форкамера", "12.0")], "ОВ")]
    result = cross_check_rooms(before, after)
    signals = signals_from_room_cross_check(result.findings)
    assert signals and signals[0].source == "room_registry"
    assert signals[0].key == "012"
    assert signals[0].domain == "room"


def test_room_name_change_stays_diagnostic_not_control_signal():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "147", "Лаборантская", "19.6")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "147", "Обрывок подписи", "19.6")], "ОВ")]
    result = cross_check_rooms(before, after)
    assert any(f.finding_type == "name_changed" for f in result.findings)
    assert signals_from_room_cross_check(result.findings) == []


def test_adapter_from_equip_cross_check_feeds_qty_signal():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ", equipment_facts=[ef(1, "14", "Приточная установка", "1")])]
    result = cross_check_equipment(before, after)
    signals = signals_from_equip_cross_check(result.findings)
    assert signals and signals[0].source == "equip_registry"
    assert signals[0].key == "14"
    assert signals[0].domain == "equipment"


def test_equipment_added_only_in_rd_does_not_create_control_signal():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[ef(1, "14", "Установка", "1")])]
    after = [DocumentInput(
        "rd.pdf", 1, [], [], "ОВ",
        equipment_facts=[ef(1, "14", "Установка", "1"), ef(1, "999", "Шумная подпись", "1")],
    )]
    result = cross_check_equipment(before, after)
    assert any(f.finding_type == "missing_in_pd" and f.equip_key == "999" for f in result.findings)
    assert all(s.key != "999" for s in signals_from_equip_cross_check(result.findings))


def test_adapter_from_requirement_cross_check_feeds_triangulation():
    pd_requirements = [Requirement(rooms=["270"], page=10, sentence="требование без кода", code=None)]
    after = [DocumentInput("rd.pdf", 1, text_facts=[{"page": 1, "text": "ничего похожего"}])]
    result = cross_check_requirements(pd_requirements, after)
    signals = signals_from_requirement_cross_check(result.findings)
    assert signals and signals[0].source == "requirement_prose"
    assert signals[0].key == "270"
    assert signals[0].domain == "room"


def test_adapter_from_routing_diff_only_uses_finding_categories():
    diff = {
        "renumbered": [{"room_key": "999"}],
        "retargeted": [{"room_key": "147"}],
        "connection_count_changed": [{"room_key": "198"}],
        "unchanged": [{"room_key": "998"}],
        "unusable": [{"room_key": "997"}],
        "room_only_before": [{"room_key": "996"}],
        "room_only_after": [],
    }
    signals = signals_from_routing_diff(diff)
    assert {s.key for s in signals} == {"147", "198"}
    assert all(s.source == "routing" for s in signals)


def test_visual_requirement_absence_becomes_independent_signal_per_room():
    results = [
        {
            "rooms": ["267", "270"],
            "verdict": "absent",
            "reason": "контур системы не показан",
            "where": "помещения 267/270",
        },
        {"rooms": ["271"], "verdict": "confirmed", "reason": "видно"},
        {"rooms": ["272"], "verdict": "unclear", "reason": "не тот лист"},
    ]
    signals = signals_from_visual_requirement_checks(results)
    assert {s.key for s in signals} == {"267", "270"}
    assert all(s.source == "vision" and s.domain == "room" for s in signals)
    assert all("контур системы" in s.detail for s in signals)


def test_requirement_plus_visual_absence_confirms_same_room():
    pd_requirements = [Requirement(
        rooms=["270"], page=10,
        sentence="В помещении 270 предусмотрена система подогрева пола",
        code=None,
    )]
    after = [DocumentInput("rd.pdf", 1, text_facts=[{"page": 1, "text": "270"}])]
    req = cross_check_requirements(pd_requirements, after)
    signals = signals_from_requirement_cross_check(req.findings)
    signals += signals_from_visual_requirement_checks([
        {"rooms": ["270"], "verdict": "absent", "reason": "на плане нет системы"}
    ])
    result = triangulate(signals)
    assert result[0].status == CONFIRMED
    assert set(result[0].sources) == {"requirement_prose", "vision"}


def test_end_to_end_two_independent_modules_confirm_same_room():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "147", "Лаборантская", "19.6")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "999", "Другое помещение", "19.6")], "ОВ")]
    room_result = cross_check_rooms(before, after)
    routing_diff = {
        "retargeted": [{"room_key": "147"}],
        "connection_count_changed": [], "renumbered": [], "unchanged": [],
        "unusable": [], "room_only_before": [], "room_only_after": [],
    }
    signals = signals_from_room_cross_check(room_result.findings) + signals_from_routing_diff(routing_diff)
    result = triangulate(signals)
    by_key = {c.key: c for c in result}
    assert by_key["147"].status == CONFIRMED
    assert set(by_key["147"].sources) == {"room_registry", "routing"}


def test_signal_from_vision_verdict_uses_consistent_source_name():
    signal = signal_from_vision_verdict("140", detail="модель отметила расхождение")
    assert signal.source == "vision"
    assert signal.domain == "room"
    assert signal.key == "140"
