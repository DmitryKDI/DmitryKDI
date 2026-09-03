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
    print("OK: два разных источника по одному ключу дают confirmed")


def test_single_source_is_only_candidate():
    signals = [Signal(source="text", domain="room", key="147", detail="а")]
    result = triangulate(signals)
    assert result[0].status == CANDIDATE, result
    print("OK: единственный источник даёт candidate, не confirmed")


def test_repeated_hits_from_same_source_do_not_double_count():
    """Реальный случай: у нарушения №1 (раздвоенная позиция П17.1/17.2) был
    только графический источник — но сама схема могла дать несколько
    отдельных наблюдений. Повторные сигналы ОДНОГО источника не должны
    случайно перейти порог в 2 источника."""
    signals = [
        Signal(source="schema", domain="room", key="012", detail="наблюдение 1"),
        Signal(source="schema", domain="room", key="012", detail="наблюдение 2"),
    ]
    result = triangulate(signals)
    assert result[0].status == CANDIDATE, result
    assert result[0].sources == ("schema",), result
    print("OK: несколько сигналов одного источника не удваивают уверенность")


def test_room_and_equipment_domains_do_not_collide():
    """Номер помещения и код позиции оборудования могут случайно совпасть
    строкой — домены должны считаться раздельно."""
    signals = [
        Signal(source="room_registry", domain="room", key="012"),
        Signal(source="equip_registry", domain="equipment", key="012"),
    ]
    result = triangulate(signals)
    assert len(result) == 2, result
    statuses = {(c.domain, c.key): c.status for c in result}
    assert statuses[("room", "012")] == CANDIDATE, statuses
    assert statuses[("equipment", "012")] == CANDIDATE, statuses
    print("OK: помещение и позиция оборудования с одинаковым ключом не смешиваются")


def test_min_sources_threshold_is_configurable():
    signals = [
        Signal(source="a", domain="room", key="1"),
        Signal(source="b", domain="room", key="1"),
        Signal(source="c", domain="room", key="1"),
    ]
    assert triangulate(signals, min_sources=4)[0].status == CANDIDATE
    assert triangulate(signals, min_sources=3)[0].status == CONFIRMED
    assert triangulate(signals, min_sources=2)[0].status == CONFIRMED
    print("OK: порог числа источников настраивается вызывающим кодом")


def test_confirmed_only_and_candidates_only_filters():
    confirmations = [
        Confirmation(domain="room", key="1", status=CONFIRMED, sources=("a", "b")),
        Confirmation(domain="room", key="2", status=CANDIDATE, sources=("a",)),
    ]
    assert [c.key for c in confirmed_only(confirmations)] == ["1"]
    assert [c.key for c in candidates_only(confirmations)] == ["2"]
    print("OK: фильтры confirmed_only/candidates_only разделяют статусы")


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


def test_adapter_from_room_cross_check_feeds_triangulation():
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "012", "Венткамера", "15.2")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "013", "Форкамера", "12.0")], "ОВ")]
    result = cross_check_rooms(before, after)
    signals = signals_from_room_cross_check(result.findings)
    assert signals and signals[0].source == "room_registry"
    assert signals[0].key == "012"
    assert signals[0].domain == "room"
    print("OK: адаптер room_cross_check отдаёт корректно оформленные сигналы")


def test_adapter_from_equip_cross_check_feeds_triangulation():
    before = [DocumentInput("pd.pdf", 1, [], [], "ОВ", equipment_facts=[ef(1, "14", "Приточная установка", "2")])]
    after = [DocumentInput("rd.pdf", 1, [], [], "ОВ", equipment_facts=[ef(1, "14", "Приточная установка", "1")])]
    result = cross_check_equipment(before, after)
    signals = signals_from_equip_cross_check(result.findings)
    assert signals and signals[0].source == "equip_registry"
    assert signals[0].key == "14"
    assert signals[0].domain == "equipment"
    print("OK: адаптер equip_cross_check отдаёт корректно оформленные сигналы")


def test_adapter_from_requirement_cross_check_feeds_triangulation():
    pd_requirements = [Requirement(rooms=["270"], page=10, sentence="требование без кода", code=None)]
    after = [DocumentInput("rd.pdf", 1, text_facts=[{"page": 1, "text": "ничего похожего"}])]
    result = cross_check_requirements(pd_requirements, after)
    signals = signals_from_requirement_cross_check(result.findings)
    assert signals and signals[0].source == "requirement_prose"
    assert signals[0].key == "270"
    assert signals[0].domain == "room"
    print("OK: адаптер requirement_cross_check отдаёт корректно оформленные сигналы")



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
    keys = {s.key for s in signals}
    assert keys == {"147", "198"}, keys
    assert all(s.source == "routing" for s in signals)
    print("OK: адаптер routing_diff берёт только retargeted/connection_count_changed")


def test_end_to_end_two_independent_modules_confirm_same_room():
    """Ровно та ситуация, которую правило должно поймать: находка по
    помещению видна и в реестре помещений, и в графе маршрутизации —
    независимо друг от друга, поэтому вместе они дают confirmed."""
    before = [DocumentInput("pd.pdf", 1, [], [rf(1, "147", "Лаборантская", "19.6")], "ОВ")]
    after = [DocumentInput("rd.pdf", 1, [], [rf(1, "147", "Другое название", "19.6")], "ОВ")]
    room_result = cross_check_rooms(before, after)
    routing_diff = {"retargeted": [{"room_key": "147"}], "connection_count_changed": [],
                     "renumbered": [], "unchanged": [], "unusable": [],
                     "room_only_before": [], "room_only_after": []}

    signals = signals_from_room_cross_check(room_result.findings) + signals_from_routing_diff(routing_diff)
    result = triangulate(signals)
    by_key = {c.key: c for c in result}
    assert by_key["147"].status == CONFIRMED, by_key["147"]
    assert set(by_key["147"].sources) == {"room_registry", "routing"}, by_key["147"]
    print("OK: два независимых модуля по одному помещению дают confirmed сквозным путём")


def test_signal_from_vision_verdict_uses_consistent_source_name():
    s = signal_from_vision_verdict("140", detail="модель отметила расхождение")
    assert s.source == "vision"
    assert s.domain == "room"
    assert s.key == "140"
    print("OK: сигнал из зрения оформлен тем же способом, что остальные источники")


if __name__ == "__main__":
    test_two_independent_sources_confirm()
    test_single_source_is_only_candidate()
    test_repeated_hits_from_same_source_do_not_double_count()
    test_room_and_equipment_domains_do_not_collide()
    test_min_sources_threshold_is_configurable()
    test_confirmed_only_and_candidates_only_filters()
    test_adapter_from_room_cross_check_feeds_triangulation()
    test_adapter_from_equip_cross_check_feeds_triangulation()
    test_adapter_from_requirement_cross_check_feeds_triangulation()
    test_adapter_from_routing_diff_only_uses_finding_categories()
    test_end_to_end_two_independent_modules_confirm_same_room()
    test_signal_from_vision_verdict_uses_consistent_source_name()
    print("ALL PASS")
