from app.anchors import normalize_room_key, normalize_room_references


def test_composite_room_reference_expands_without_external_knowledge():
    assert normalize_room_references(["пом. 101, 102, 103.1"]) == ["101", "102", "103.1"]


def test_leading_zeroes_are_preserved():
    assert normalize_room_references(["помещение 012"]) == ["012"]
    assert normalize_room_key("room 012.1") == "012.1"


def test_named_zone_is_kept_when_no_numeric_anchor_exists():
    assert normalize_room_references(["Медицинские помещения"]) == ["медицинские помещения"]
