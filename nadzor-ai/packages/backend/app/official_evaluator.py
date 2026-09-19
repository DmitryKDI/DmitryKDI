"""Оценка атомарных evidence_group по критериям матрицы 1.1.

Вход — словари с object_id, parameter_code, location, finding_type,
finding_status, completeness_status и evidence. Источник evidence содержит
role (expected/actual), file_id, sha256, page (с единицы), bbox.
Рамки нормализованы в [0;1] уже после учёта CropBox/MediaBox/Rotate: перевод
координат относится к извлечению источника, а не к сопоставлению результатов.

GOLD требует expert_id и timestamp; кандидаты и незавершённые проверки
исключаются явно. Непроверенная отрицательная группа не становится TN.
Порог локализации задан листом «МЕТРИКИ», а не подобран по одному комплекту.
Это проверка формата и метрик, а не утверждение о достижении приёмочных порогов.
"""

import json
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal

from .parameter_catalog import get_parameter

MIN_BBOX_IOU = 0.5
_DECISIONS = {"CONFIRMED_VIOLATION", "NEGATIVE_VERIFIED"}
_SPLITS = {"TRAIN", "VALIDATION", "HIDDEN_TEST"}


def _identity(row: Mapping) -> tuple:
    for field in ("object_id", "parameter_code", "location", "finding_type"):
        if not row.get(field):
            raise ValueError(f"Отсутствует обязательное поле {field}")
    try:
        get_parameter(row["parameter_code"])
    except KeyError as exc:
        raise ValueError("Неизвестный parameter_code") from exc
    # Структурированная локализация сравнивается без потери номера/обозначения.
    return tuple(json.dumps(row[field], sort_keys=True, ensure_ascii=False) for field in
                 ("object_id", "parameter_code", "location", "finding_type"))


def _validate_splits(rows: Sequence[Mapping]) -> None:
    splits = {}
    for row in rows:
        _identity(row)
        split = row.get("split")
        if split is None:
            continue
        if split not in _SPLITS:
            raise ValueError("Неизвестный split")
        object_id = row["object_id"]
        if object_id in splits and splits[object_id] != split:
            raise ValueError("Object-level split leakage: объект попал в разные наборы")
        splits[object_id] = split


def _bbox(value) -> tuple | None:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v)
           or not 0 <= v <= 1 for v in value):
        return None
    left, top, right, bottom = value
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _iou(a, b) -> float:
    # Десятичные координаты на границе IoU не должны проиграть из-за float 0.499999…
    left, top, right, bottom = (Decimal(str(value)) for value in a)
    other_left, other_top, other_right, other_bottom = (Decimal(str(value)) for value in b)
    area_a = (right - left) * (bottom - top)
    area_b = (other_right - other_left) * (other_bottom - other_top)
    intersection = (max(0, min(right, other_right) - max(left, other_left))
                    * max(0, min(bottom, other_bottom) - max(top, other_top)))
    return float(intersection / (area_a + area_b - intersection))


def _valid_sources(row: Mapping) -> bool:
    sources = row.get("evidence")
    if not isinstance(sources, list) or not sources:
        return False
    roles = set()
    for source in sources:
        if not isinstance(source, Mapping):
            return False
        if source.get("role") not in {"expected", "actual"}:
            return False
        roles.add(source["role"])
        if any(not isinstance(source.get(field), str) or not source[field]
               for field in ("file_id", "sha256")):
            return False
        page = source.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            return False
        if _bbox(source.get("bbox")) is None:
            return False
    return roles == {"expected", "actual"}


def _matching(edges: list[list[int]]) -> dict[int, int]:
    """Максимальное паросочетание не зависит от жадного порядка перекрывающихся рамок."""
    assigned = {}

    def augment(left, seen):
        for right in edges[left]:
            if right in seen:
                continue
            seen.add(right)
            if right not in assigned or augment(assigned[right], seen):
                assigned[right] = left
                return True
        return False

    for left in range(len(edges)):
        augment(left, set())
    return assigned


def _evidence_matches(prediction: Mapping, gold: Mapping) -> bool:
    if not _valid_sources(prediction):
        return False
    sources = prediction["evidence"]
    expected = gold["evidence"]
    edges = [
        [index for index, actual in enumerate(sources)
         if all(actual[field] == target[field] for field in ("role", "file_id", "sha256", "page"))
         and _iou(actual["bbox"], target["bbox"]) >= MIN_BBOX_IOU]
        for target in expected
    ]
    return len(_matching(edges)) == len(expected)


def _decision(row: Mapping) -> bool:
    return row.get("completeness_status") == "COMPLETE" and row.get("finding_status") in _DECISIONS


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def evaluate(predictions: Sequence[Mapping], gold: Sequence[Mapping]) -> dict:
    """P/R/F1, FPR и покрытие; отсутствие знаменателя возвращает None, не успех.

    Лишнее положительное предсказание — FP, даже если оно дублирует верное.
    FPR считается по отрицательным группам с решением: ложные тревоги против
    явно подтверждённых отрицательных, с отдельным negative_coverage.
    coverage отражает наличие завершённого решения, localization_completeness
    отдельно отражает наличие точных источников и рамок независимо от статуса.
    Предсказания на непроверенных GOLD-группах исключаются из оценки явно.
    """
    predictions, gold = list(predictions), list(gold)
    _validate_splits(predictions + gold)
    accepted, excluded = [], []
    ids = set()
    for row in gold:
        group_id = row.get("evidence_group_id")
        if not group_id or group_id in ids:
            raise ValueError("GOLD evidence_group_id должен быть уникальным и непустым")
        ids.add(group_id)
        reviewed = bool(row.get("expert_id") and row.get("timestamp"))
        if not reviewed or not _decision(row):
            excluded.append(row)
        else:
            if not _valid_sources(row):
                raise ValueError("GOLD содержит неполное или некорректное evidence")
            accepted.append(row)
    accepted_keys = {_identity(row) for row in accepted}
    excluded_keys = {_identity(row) for row in excluded} - accepted_keys
    scored = [row for row in predictions if _identity(row) not in excluded_keys]
    decisive = [row for row in scored if _decision(row)]
    positive = [row for row in decisive if row["finding_status"] == "CONFIRMED_VIOLATION"]
    positive_gold = [row for row in accepted if row["finding_status"] == "CONFIRMED_VIOLATION"]
    negative_gold = [row for row in accepted if row["finding_status"] == "NEGATIVE_VERIFIED"]
    edges = [[index for index, target in enumerate(positive_gold)
              if _identity(row) == _identity(target) and _evidence_matches(row, target)]
             for row in positive]
    matched = _matching(edges)
    tp, fp = len(matched), len(positive) - len(matched)
    fn = len(positive_gold) - tp
    negative_fp = tn = 0
    for target in negative_gold:
        candidates = [row for row in decisive if _identity(row)[:3] == _identity(target)[:3]]
        # Даже неверная рамка не отменяет ложную тревогу на известной отрицательной группе.
        if any(row["finding_status"] == "CONFIRMED_VIOLATION" for row in candidates):
            negative_fp += 1
        elif any(_evidence_matches(row, target) for row in candidates):
            tn += 1
    covered = sum(any(_identity(row) == _identity(target) for row in decisive)
                  for target in accepted)
    localized = sum(any(_identity(row) == _identity(target) and _evidence_matches(row, target)
                        for row in decisive) for target in accepted)
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "negative_fp": negative_fp,
        "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "fpr": _ratio(negative_fp, negative_fp + tn),
        "coverage": _ratio(covered, len(accepted)),
        "abstention_rate": _ratio(len(accepted) - covered, len(accepted)),
        "negative_coverage": _ratio(negative_fp + tn, len(negative_gold)),
        "localization_completeness": _ratio(localized, len(accepted)),
        "gold_groups": len(accepted), "excluded_gold_groups": len(excluded),
        "ignored_predictions": len(predictions) - len(scored),
        "matched_group_ids": sorted(positive_gold[index]["evidence_group_id"] for index in matched),
        "bbox_iou_threshold": MIN_BBOX_IOU,
    }
