"""Метрики качества для повторяемых benchmark-прогонов НАДЗОР.ИИ.

Модуль намеренно не содержит конкретных эталонных нарушений: ground truth
передаётся снаружи (JSON/тест), поэтому боевой код не знает правильных
ответов тестового комплекта и не может случайно подстроиться под них.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class BenchmarkMetrics:
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict[str, int | float]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


def _normalise(values: Iterable[str]) -> set[str]:
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def classification_metrics(expected: Iterable[str], found: Iterable[str]) -> BenchmarkMetrics:
    """Посчитать TP/FP/FN, precision, recall и F1 по ключам находок.

    Ключом может быть стабильный id benchmark-кейса, например
    ``ventilation-room-140``. Сопоставление семантического текста с эталоном
    остаётся ответственностью вызывающего benchmark-скрипта: эта функция
    только считает метрики по уже нормализованным идентификаторам.
    """
    expected_set = _normalise(expected)
    found_set = _normalise(found)

    tp = len(expected_set & found_set)
    fp = len(found_set - expected_set)
    fn = len(expected_set - found_set)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return BenchmarkMetrics(
        tp=tp,
        fp=fp,
        fn=fn,
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
    )


def build_benchmark_summary(
    expected: Iterable[str],
    found: Iterable[str],
    *,
    duration_seconds: float | None = None,
    llm_metrics: dict | None = None,
) -> dict:
    """Собрать компактный benchmark-friendly блок для JSON-лога."""
    metrics = classification_metrics(expected, found).as_dict()
    summary: dict = {"quality": metrics}
    if duration_seconds is not None:
        summary["duration_seconds"] = round(float(duration_seconds), 6)
    if llm_metrics is not None:
        summary["llm"] = {
            "requests": llm_metrics.get("requests", 0),
            "errors": llm_metrics.get("errors", 0),
            "rate_limits": llm_metrics.get("rate_limits", 0),
            "result_cache_hits": llm_metrics.get("result_cache_hits", 0),
            "persistent_cache_hits": llm_metrics.get("persistent_cache_hits", 0),
        }
    return summary
