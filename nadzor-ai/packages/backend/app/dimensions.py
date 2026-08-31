"""Параметры/размеры у любого якоря — «геометрия и параметры» из общей
механики (Г.5, Г.30 п.2/п.3), не только у номера помещения.

Размер сечения воздуховода («800x400»), диаметр трубы («∅200»), мощность
оборудования («Nэл.=34,0 кВт») — тот же класс факта, что и баланс-рамка
(`balance_box.py`), только без обязательной привязки к номеру помещения:
на листах без экспликации (сечения, узлы, конструктивные планы) единственный
доступный якорь — ось (`axes.py`) или вообще только координата
(`coord_registry.py`).

Прямая проверка на реальном листе (Г.11, тот же лист, что и в
`routing_graph.py`/`axes.py`): размеров воздуховодов в текстовом слое НЕТ
вообще — ноль совпадений на всей странице при богатой векторной графике.
Тот же случай, что уже дважды встретился (`balance_box.py`, ветки в
`routing_graph.py`): подписи размеров на этом комплекте — CAD-текст в
кривых (Г.13). Текстовый путь здесь — защитный быстрый путь на случай
другого экспорта/автора, не основной; основной — `dimension_vision.py`."""
from __future__ import annotations

import re

_SECTION_RE = re.compile(r"^\d{2,4}[xх]\d{2,4}$", re.I)  # "800x400", "200х100"
_DIAMETER_RE = re.compile(r"^[Øø]\d{2,4}$")
_POWER_RE = re.compile(r"^N[эе]л\.?=[\d,.]+$", re.I)  # "Nэл.=34,0"
_VOLTAGE_RE = re.compile(r"^U=\d[xх]\d+\s?[ВB]?$", re.I)  # "U=3х380"

_KIND_SECTION = "section"
_KIND_DIAMETER = "diameter"
_KIND_POWER = "power"
_KIND_VOLTAGE = "voltage"

_PATTERNS = (
    (_SECTION_RE, _KIND_SECTION),
    (_DIAMETER_RE, _KIND_DIAMETER),
    (_POWER_RE, _KIND_POWER),
    (_VOLTAGE_RE, _KIND_VOLTAGE),
)


def extract_dimension_facts(text: str) -> list[dict]:
    """Возвращает [{value, kind}] — параметрические подписи, найденные в
    текстовом слое листа целиком (без привязки к конкретному якорю: это
    делает вызывающий код через геометрию — ближайший якорь любого типа,
    room/axis/elevation — по тем же координатам, что и `routing_graph.py`'s
    `_nearest_anchor`).

    На CAD-листах этого комплекта ожидаемо пустой список — см. докстринг
    модуля. Пустой результат — видимое состояние («в тексте не нашлось»),
    не «размеров на листе нет» (Г.10)."""
    facts: list[dict] = []
    for line in text.splitlines():
        token = line.strip()
        if not token:
            continue
        for pattern, kind in _PATTERNS:
            if pattern.match(token):
                facts.append({"value": token, "kind": kind})
                break
    return facts
