"""Официальная матрица хранится отдельно от механики и возвращается без общего состояния."""

from collections import Counter

import pytest
from app.parameter_catalog import get_parameter, list_parameters


def test_official_catalog_has_all_unique_versioned_parameters():
    parameters = list_parameters()
    assert len(parameters) == 132
    assert [item["code"] for item in parameters] == [f"M-{n:03d}" for n in range(1, 133)]
    assert Counter(item["priority"] for item in parameters) == {"HIGH": 106, "MEDIUM": 26}
    for item in parameters:
        assert item["version"] == "1.1"
        for field in ("description", "unit", "section", "source_pd", "source_rd", "source_id",
                      "trigger"):
            assert isinstance(item[field], str) and item[field]
    print("OK: каталог содержит 132 уникальных параметра официальной версии 1.1")


def test_returned_catalog_cannot_change_later_reads():
    item = get_parameter("M-001")
    original = item["description"]
    item["description"] = "changed"
    listing = list_parameters()
    listing.clear()
    assert get_parameter("M-001")["description"] == original
    assert len(list_parameters()) == 132
    with pytest.raises(KeyError):
        get_parameter("unknown")
    print("OK: изменение ответа не меняет каталог, неизвестный код отклоняется")
