"""Разбор значений из текста документов: классы материалов, объёмы, даты."""
from __future__ import annotations

import re
from datetime import date, datetime

# В документах кириллические и латинские буквы смешаны: «В25» и «B25» — одно и то же.
_CYR_TO_LAT = str.maketrans("АВСЕКМНОРТХасеорху", "ABCEKMHOPTXaceopxy")

RE_CONCRETE = re.compile(r"\bB\s?(\d{1,3})\b")
RE_FROST = re.compile(r"\bF\s?(\d{2,3})\b")
RE_WATER = re.compile(r"\bW\s?(\d{1,2})\b")
RE_REBAR = re.compile(r"\bA\s?(\d{3})\s?([CSТ]?)\b")
RE_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
RE_NUMBER = re.compile(r"(\d+(?:[.,]\d+)?)")
RE_AREA = re.compile(r"площад[ьи]\s*(\d+(?:[.,]\d+)?)")
RE_THICKNESS = re.compile(r"толщин[аы]\s*(\d+(?:[.,]\d+)?)")


def latinize(text: str) -> str:
    """Привести смешанную кириллицу к латинице для сравнения обозначений."""
    return (text or "").translate(_CYR_TO_LAT)


def concrete_class(text: str) -> str:
    """Класс бетона по прочности на сжатие, например B25."""
    m = RE_CONCRETE.search(latinize(text).upper())
    return f"B{m.group(1)}" if m else ""


def frost_grade(text: str) -> str:
    m = RE_FROST.search(latinize(text).upper())
    return f"F{m.group(1)}" if m else ""


def water_grade(text: str) -> str:
    m = RE_WATER.search(latinize(text).upper())
    return f"W{m.group(1)}" if m else ""


def rebar_class(text: str) -> str:
    """Класс арматуры, например A500C."""
    m = RE_REBAR.search(latinize(text).upper())
    if not m:
        return ""
    return f"A{m.group(1)}{m.group(2)}".strip()


def grade_value(grade: str) -> float:
    """Числовая часть марки для сравнения: B25 → 25, F150 → 150."""
    m = RE_NUMBER.search(grade or "")
    return float(m.group(1).replace(",", ".")) if m else 0.0


def parse_ru_date(text: str) -> date | None:
    m = RE_DATE.search(text or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_amount(text: str) -> tuple[float, str]:
    """Число и единица измерения: «142,0 м³» → (142.0, 'м³')."""
    if not text:
        return 0.0, ""
    m = RE_NUMBER.search(text)
    value = float(m.group(1).replace(",", ".")) if m else 0.0
    unit = ""
    for candidate in ("м³", "м3", "м²", "м2", "т", "шт", "пог.м", "м"):
        if candidate in text:
            unit = candidate
            break
    return value, unit


def parse_geometry(text: str) -> dict:
    """Геометрия участка: площадь в м² и толщина в мм."""
    low = (text or "").lower()
    area = RE_AREA.search(low)
    thickness = RE_THICKNESS.search(low)
    return {
        "area_m2": float(area.group(1).replace(",", ".")) if area else 0.0,
        "thickness_mm": float(thickness.group(1).replace(",", ".")) if thickness else 0.0,
    }


def deviation_percent(actual: float, expected: float) -> float:
    if expected <= 0:
        return 0.0
    return round(abs(actual - expected) / expected * 100, 1)


def rule_enabled(config: dict, name: str) -> bool:
    return bool(config.get("rules", {}).get(name, {}).get("enabled", True))


def rule_severity(config: dict, name: str, default: str = "major") -> str:
    return config.get("rules", {}).get(name, {}).get("severity", default)
