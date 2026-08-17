"""Классификация сообщений: является ли текст тёплым лидом на ремонт.

Триггерные фразы сопоставляются гибко: между словами фразы допускается
до `max_gap_words` произвольных слов (в том же порядке), чтобы ловить
живую речь вроде "посоветуйте, пожалуйста, бригаду" для триггера
"посоветуйте бригаду". Стоп-слова (реклама/исполнитель) сопоставляются
строго, точной подстрокой — здесь важнее не отсеять настоящий лид
по случайному частичному совпадению.
"""

import json
import re

DEFAULT_MAX_GAP_WORDS = 2


def load_keywords(path="keywords.json"):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("triggers", []), data.get("stop_words", [])


def _normalize(text):
    """Заменяет пунктуацию на пробелы, чтобы 'посоветуйте, пожалуйста,' не мешала
    сопоставлению фразы из-за отсутствия пробела перед знаком препинания."""
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _compile_exact(phrase):
    return re.compile(re.escape(phrase), re.IGNORECASE | re.UNICODE)


def _compile_flexible(phrase, max_gap_words):
    words = [re.escape(w) for w in phrase.split()]
    if len(words) <= 1:
        return re.compile(rf"\b{words[0]}\b", re.IGNORECASE | re.UNICODE)

    gap = rf"\s+(?:\S+\s+){{0,{max_gap_words}}}"
    pattern = rf"\b{words[0]}\b"
    for word in words[1:]:
        pattern += gap + rf"\b{word}\b"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


class LeadFilter:
    def __init__(self, keywords_path="keywords.json", max_gap_words=DEFAULT_MAX_GAP_WORDS):
        self.trigger_phrases, self.stop_phrases = load_keywords(keywords_path)
        self._trigger_patterns = [
            (_compile_exact(p), _compile_flexible(p, max_gap_words)) for p in self.trigger_phrases
        ]
        self._stop_patterns = [_compile_exact(p) for p in self.stop_phrases]

    def classify(self, text):
        """Возвращает (is_lead: bool, matched_keywords: list[str], confidence: float)."""
        if not text:
            return False, [], 0.0

        normalized = _normalize(text)

        for pattern in self._stop_patterns:
            if pattern.search(normalized):
                return False, [], 0.0

        matched = []
        loose_matches = 0
        for phrase, (exact_pattern, flexible_pattern) in zip(
            self.trigger_phrases, self._trigger_patterns
        ):
            if exact_pattern.search(normalized):
                matched.append(phrase)
            elif flexible_pattern.search(normalized):
                matched.append(phrase)
                loose_matches += 1

        if not matched:
            return False, [], 0.0

        confidence = 0.4 + 0.2 * len(matched) - 0.05 * loose_matches
        confidence = max(0.3, min(1.0, confidence))
        return True, matched, round(confidence, 2)
