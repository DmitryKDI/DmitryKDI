"""Классификация сообщений: является ли текст тёплым лидом на ремонт."""

import json
import re


def load_keywords(path="keywords.json"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("triggers", []), data.get("stop_words", [])


def _compile_patterns(phrases):
    return [re.compile(re.escape(p), re.IGNORECASE | re.UNICODE) for p in phrases]


class LeadFilter:
    def __init__(self, keywords_path="keywords.json"):
        self.trigger_phrases, self.stop_phrases = load_keywords(keywords_path)
        self._trigger_patterns = _compile_patterns(self.trigger_phrases)
        self._stop_patterns = _compile_patterns(self.stop_phrases)

    def classify(self, text):
        """Возвращает (is_lead: bool, matched_keywords: list[str], confidence: float)."""
        if not text:
            return False, [], 0.0

        for pattern in self._stop_patterns:
            if pattern.search(text):
                return False, [], 0.0

        matched = [
            phrase
            for phrase, pattern in zip(self.trigger_phrases, self._trigger_patterns)
            if pattern.search(text)
        ]
        if not matched:
            return False, [], 0.0

        confidence = min(1.0, 0.4 + 0.2 * len(matched))
        return True, matched, round(confidence, 2)
