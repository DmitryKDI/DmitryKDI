"""Синтетические группы проверяют полноту доказательств независимо от ответа модели."""

from copy import deepcopy

import pytest
from app.official_evaluator import evaluate


def group(key="one", status="CONFIRMED_VIOLATION", **extra):
    return {
        "evidence_group_id": key, "object_id": "synthetic-object", "parameter_code": "M-001",
        "location": key, "finding_type": "value_mismatch", "finding_status": status,
        "completeness_status": "COMPLETE", "expert_id": "synthetic-reviewer",
        "timestamp": "2026-01-01T00:00:00Z", "split": "HIDDEN_TEST",
        "evidence": [
            {"role": role, "file_id": role, "sha256": role, "page": 1,
             "bbox": [0.1, 0.1, 0.5, 0.5]}
            for role in ("expected", "actual")
        ], **extra,
    }


def test_complete_match_scores_and_duplicate_is_false_positive():
    gold = group()
    report = evaluate([deepcopy(gold), deepcopy(gold)], [gold])
    assert (report["tp"], report["fp"], report["fn"]) == (1, 1, 0)
    assert report["precision"] == 0.5
    assert report["recall"] == 1
    assert report["coverage"] == 1
    print("OK: одна эталонная группа засчитывается однократно")


@pytest.mark.parametrize("field,value", [
    ("file_id", "other"), ("sha256", "other"), ("page", 2),
    ("bbox", [0.6, 0.6, 0.9, 0.9]), ("bbox", [0.1, 0.1, float("nan"), 0.5]),
])
def test_wrong_source_or_localization_cannot_confirm(field, value):
    gold = group()
    prediction = deepcopy(gold)
    prediction["evidence"][0][field] = value
    report = evaluate([prediction], [gold])
    assert (report["tp"], report["fp"], report["fn"]) == (0, 1, 1)
    assert report["localization_completeness"] == 0
    print("OK: неверный источник или координаты не засчитываются как доказательство")


def test_incomplete_and_candidate_are_abstentions_and_not_negative_results():
    gold = [group("positive"), group("negative", "NEGATIVE_VERIFIED")]
    predictions = [group("positive", completeness_status="MISSING_EVIDENCE"),
                   group("negative", "CANDIDATE")]
    report = evaluate(predictions, gold)
    assert report["coverage"] == 0
    assert report["abstention_rate"] == 1
    assert report["fn"] == 1
    assert report["tn"] == 0
    assert report["fpr"] is None
    print("OK: незавершённая проверка и кандидат не превращаются в отрицательный результат")


def test_false_positive_rate_uses_reviewed_negative_groups():
    gold = [group("a", "NEGATIVE_VERIFIED"), group("b", "NEGATIVE_VERIFIED"),
            group("c", "NEGATIVE_VERIFIED")]
    predictions = [group("a"), group("a"), group("b", "NEGATIVE_VERIFIED")]
    report = evaluate(predictions, gold)
    assert report["fp"] == 2
    assert report["negative_fp"] == 1
    assert report["tn"] == 1
    assert report["fpr"] == 0.5
    assert report["negative_coverage"] == pytest.approx(2 / 3)
    print("OK: FPR считает отрицательные группы, воздержание не добавляет TN")


def test_wrong_finding_type_still_causes_false_alarm_on_negative_group():
    report = evaluate([group(finding_type="different_type")], [group(status="NEGATIVE_VERIFIED")])
    assert report["negative_fp"] == 1
    assert report["fpr"] == 1
    print("OK: неверный тип ложной тревоги не скрывает ошибку на отрицательной группе")


def test_unreviewed_or_candidate_gold_is_not_a_training_label():
    gold = [group("a", "CANDIDATE"), group("b", expert_id=""),
            group("c", completeness_status="NOT_COMPARABLE")]
    report = evaluate([group("a"), group("b"), group("c")], gold)
    assert report["gold_groups"] == 0
    assert report["excluded_gold_groups"] == 3
    assert report["fp"] == 0
    assert report["precision"] is None
    assert report["coverage"] is None
    print("OK: неподтверждённые и несопоставимые эталоны исключаются явно")


def test_object_split_leakage_is_rejected_even_for_excluded_rows():
    with pytest.raises(ValueError, match="split"):
        evaluate([group(split="TRAIN")], [group()])
    with pytest.raises(ValueError, match="split"):
        evaluate([], [group("a", "CANDIDATE", split="TRAIN"), group("b")])
    print("OK: все редакции объекта обязаны оставаться в одном наборе")


@pytest.mark.parametrize("field,value", [("object_id", "other"), ("parameter_code", "M-002"),
                                         ("location", "other"), ("finding_type", "other")])
def test_wrong_identity_is_not_rescued_by_correct_box(field, value):
    gold = group()
    prediction = deepcopy(gold)
    prediction[field] = value
    report = evaluate([prediction], [gold])
    assert report["tp"] == 0
    assert report["fn"] == 1
    print("OK: правильная рамка не исправляет неверный объект, параметр или локализацию")


def test_missing_actual_source_and_invalid_gold_cannot_pass():
    gold = group()
    prediction = deepcopy(gold)
    prediction["evidence"].pop()
    assert evaluate([prediction], [gold])["tp"] == 0
    with pytest.raises(ValueError, match="evidence"):
        evaluate([], [prediction])
    print("OK: для полной сверки нужны обе стороны доказательства")


def test_bbox_boundary_and_empty_dataset_have_honest_metrics():
    gold = group()
    prediction = deepcopy(gold)
    for item in prediction["evidence"]:
        item["bbox"] = [0.1, 0.1, 0.3, 0.5]
    assert evaluate([prediction], [gold])["tp"] == 1
    empty = evaluate([], [])
    assert empty["f1"] is None
    assert empty["abstention_rate"] is None
    print("OK: официальный порог IoU включителен, пустая выборка не означает 100% качества")
