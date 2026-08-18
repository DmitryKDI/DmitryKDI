"""Приоритизация гипотез. Одна функция с конфигурируемыми весами.

Балл 0–100 отвечает на вопрос «куда идти в первую очередь», а не «насколько
модель уверена»: уверенность — лишь один из множителей.
"""
from __future__ import annotations

DEFAULT_WEIGHTS = {
    "severity": 0.35, "confidence": 0.20, "structural_criticality": 0.20,
    "legal_weight": 0.10, "readiness_stage": 0.10, "contractor_history": 0.05,
}

STAGE_HINTS = [
    ("скрыт", "скрытые_работы"), ("бетонир", "скрытые_работы"), ("армирован", "скрытые_работы"),
    ("монтаж", "монтаж"), ("устройство", "монтаж"),
    ("отделк", "отделка"), ("стяжк", "отделка"), ("перегород", "отделка"),
]


def _stage_key(title: str, element: dict) -> str:
    low = f"{title} {element.get('name', '')}".lower()
    for hint, key in STAGE_HINTS:
        if hint in low:
            return key
    return "default"


def score(severity: str, confidence: float, element: dict, legal_refs: list[dict],
          title: str, config: dict, contractor_history: float = 0.5) -> float:
    """Итоговый приоритет гипотезы, 0–100."""
    weights = {**DEFAULT_WEIGHTS, **config.get("weights", {})}
    severity_scores = config.get("severity_scores", {"critical": 1.0, "major": 0.6, "minor": 0.3})
    criticality = config.get("structural_criticality", {})
    legal_weights = config.get("legal_weight", {})
    stages = config.get("readiness_stage", {})

    element_name = element.get("name", "")
    article = legal_refs[0].get("article", "") if legal_refs else ""

    parts = {
        "severity": severity_scores.get(severity, 0.3),
        "confidence": max(0.0, min(confidence, 1.0)),
        "structural_criticality": criticality.get(element_name, criticality.get("default", 0.5)),
        "legal_weight": legal_weights.get(article, legal_weights.get("default", 0.5)),
        "readiness_stage": stages.get(_stage_key(title, element), stages.get("default", 0.5)),
        "contractor_history": contractor_history,
    }
    total = sum(weights.get(key, 0.0) * value for key, value in parts.items())
    return round(min(total, 1.0) * 100, 1)


def explain(severity: str, confidence: float, element: dict, legal_refs: list[dict],
            title: str, config: dict, contractor_history: float = 0.5) -> dict:
    """Раскрытие расчёта балла по клику в интерфейсе."""
    weights = {**DEFAULT_WEIGHTS, **config.get("weights", {})}
    value = score(severity, confidence, element, legal_refs, title, config, contractor_history)
    return {"total": value, "weights": weights,
            "stage": _stage_key(title, element),
            "element": element.get("name", ""),
            "article": legal_refs[0].get("article", "") if legal_refs else ""}
