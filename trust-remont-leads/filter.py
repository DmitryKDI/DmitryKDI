"""Классификация сообщений: тёплый лид на ремонт квартиры или нет."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KEYWORDS_PATH = Path(__file__).parent / "keywords.json"


@dataclass
class Verdict:
    is_lead: bool
    confidence: float
    matched_triggers: list[str] = field(default_factory=list)
    matched_stopwords: list[str] = field(default_factory=list)
    reason: str = ""


def load_keywords(path: str | Path = DEFAULT_KEYWORDS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def evaluate_message(text: str, keywords: dict) -> Verdict:
    """Считает сообщение лидом, если найдена триггерная фраза и нет стоп-слов
    (стоп-слова выдают в авторе исполнителя/рекламу, а не заказчика)."""
    if not text or not text.strip():
        return Verdict(is_lead=False, confidence=0.0, reason="пустой текст")

    normalized = _normalize(text)

    matched_stopwords = [
        phrase for phrase in keywords.get("stop_phrases", [])
        if _normalize(phrase) in normalized
    ]
    if matched_stopwords:
        return Verdict(
            is_lead=False,
            confidence=0.0,
            matched_stopwords=matched_stopwords,
            reason="похоже на рекламу исполнителя",
        )

    matched_triggers = [
        phrase for phrase in keywords.get("trigger_phrases", [])
        if _normalize(phrase) in normalized
    ]
    if not matched_triggers:
        return Verdict(is_lead=False, confidence=0.0, reason="нет триггерных фраз")

    confidence = min(1.0, 0.5 + 0.15 * (len(matched_triggers) - 1))
    return Verdict(
        is_lead=True,
        confidence=round(confidence, 2),
        matched_triggers=matched_triggers,
        reason="найдены триггерные фразы",
    )
