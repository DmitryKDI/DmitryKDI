"""Версионируемый каталог официальной матрицы; предметные правила находятся в данных."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

PARAMETER_COUNT = 132
CATALOG_VERSION = "1.1"
CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "parameter_catalog_v1_1.json"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if payload.get("version") != CATALOG_VERSION:
        raise ValueError("Неизвестная версия официальной матрицы")
    parameters = payload["parameters"]
    expected = [f"M-{number:03d}" for number in range(1, PARAMETER_COUNT + 1)]
    if [item.get("code") for item in parameters] != expected:
        raise ValueError("Каталог должен содержать все коды официальной матрицы по порядку")
    fields = ("name", "description", "section", "unit", "source_pd", "source_rd", "source_id",
              "trigger")
    for item in parameters:
        if item.get("priority") not in {"HIGH", "MEDIUM"}:
            raise ValueError("Неизвестный приоритет параметра")
        if item.get("version") != CATALOG_VERSION:
            raise ValueError("Версия параметра не совпадает с версией каталога")
        if any(not isinstance(item.get(field), str) or not item[field] for field in fields):
            raise ValueError("Не заполнено обязательное поле параметра")
    return {item["code"]: item for item in parameters}


def list_parameters() -> list[dict]:
    """Копия всех параметров: вызывающий код не может изменить общий каталог."""
    return deepcopy(list(_catalog().values()))


def get_parameter(code: str) -> dict:
    """Возвращает параметр; неизвестный код не заменяется ближайшим или вымышленным."""
    return deepcopy(_catalog()[code])
