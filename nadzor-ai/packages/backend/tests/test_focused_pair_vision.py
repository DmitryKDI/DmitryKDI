import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.focused_pair_vision as focused


def test_select_rooms_uses_numeric_room_order(monkeypatch):
    monkeypatch.setattr(focused, "MAX_FOCUSED_ROOMS_PER_PAIR", 4)
    assert focused.select_rooms(["110", "002", "12", "003", "002"]) == ["002", "003", "12", "110"]


def test_room_sort_keeps_subrooms_after_base_room():
    rooms = ["012.2", "012", "011", "012.1"]
    assert sorted(rooms, key=focused.room_sort_key) == ["011", "012", "012.1", "012.2"]
