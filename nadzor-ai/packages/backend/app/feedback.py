"""Обратная связь и управляемое дообучение (ТЗ 7, модули 4 и 10; 9.4; 14).

Решение инспектора превращается в размеченную запись:

  CONFIRMED_VIOLATION   → положительный GOLD-кандидат (черновик набора);
  NEGATIVE_VERIFIED     → отрицательный пример (черновик) + Rejection_Log;
  CLARIFICATION_REQUIRED → в GOLD не идёт, спорный случай в Dispute_Log.

В выпуск набора (dataset_version) попадают только записи, одобренные
куратором, из финализированных протоколов. Разбиение — по объектам: объект
получает набор один раз и навсегда, поэтому ни одна стройка не окажется в
TRAIN и HIDDEN_TEST одновременно ни в одном выпуске.

Модель допускается к публикации только после приёмки по порогам раздела 14.3
и без ухудшения относительно действующей модели более чем на 2 п.п.;
решение о публикации подписывает ответственное лицо, откат сохраняется.
Само обучение весов выполняется вне сервиса (на стенде с GPU); сюда
регистрируются его результаты.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter

from sqlalchemy.orm import Session

from . import models

POSITIVE = "POSITIVE"
NEGATIVE = "NEGATIVE"
LABELS = {"CONFIRMED_VIOLATION": POSITIVE, "NEGATIVE_VERIFIED": NEGATIVE}

# Приёмочные пороги (ТЗ 14.3) и допустимое ухудшение относительно действующей
# модели (ТЗ 9.4) — значения из самого ТЗ, а не подобранные.
ACCEPTANCE = {"precision": 0.90, "recall": 0.80, "f1": 0.85}
MAX_FALSE_POSITIVE_RATE = 0.10
MAX_RECALL_DROP = 0.02
MAX_FPR_GROWTH = 0.02

# Доли разбиения объектов на TRAIN / VALIDATION / HIDDEN_TEST, в процентах.
SPLIT_TRAIN_PERCENT = 70
SPLIT_VALIDATION_PERCENT = 15

# Что посоветовать по коду причины отклонения (ТЗ 7, модуль 10).
SUGGESTED_FIX = {
    "WRONG_REVISION": "проверить метаданные редакций и цепочку замены в реестре файлов",
    "APPROVED_CHANGE": "учитывать ведомость согласованных изменений при сравнении параметра",
    "OCR_ERROR": "проверить качество распознавания листов; страницы LOW_QUALITY не "
                 "использовать как единственный источник",
    "BINDING_ERROR": "проверить привязку цитаты к листу и координатам",
    "NOT_APPLICABLE": "уточнить условие применимости параметра (trigger_logic)",
    "NO_DIFFERENCE": "уточнить правило сравнения или порог параметра в матрице",
    "OTHER": "разобрать основание инспектора вручную",
}


def system_comment(status: str, reason_code: str = "") -> str:
    """Системный комментарий к решению — формулировки ТЗ 9.4."""
    if status == "NEGATIVE_VERIFIED":
        return (f"Результат инспектора: NEGATIVE_VERIFIED. Причина: {reason_code or 'не указана'}. "
                "Запись включена в черновик следующей версии набора данных; её использование "
                "для обучения допускается только после проверки куратором данных и выпуска "
                "dataset_version.")
    if status == "CLARIFICATION_REQUIRED":
        return ("Статус: CLARIFICATION_REQUIRED. Показаны точные страницы и доказательные "
                "фрагменты. До повторного решения инспектора запись не включается в GOLD и не "
                "передаётся во внешнюю систему.")
    if status == "CONFIRMED_VIOLATION":
        return ("Результат инспектора: CONFIRMED_VIOLATION. Запись сохранена как положительный "
                "GOLD-кандидат; передача наружу — только после финализации протокола, в "
                "обучение — после проверки куратором и выпуска dataset_version.")
    return "Решение инспектора сохранено отдельной версией."


TRAIN, VALIDATION, HIDDEN_TEST = "TRAIN", "VALIDATION", "HIDDEN_TEST"
SPLITS = (TRAIN, VALIDATION, HIDDEN_TEST)
_LEGACY_SPLITS = {"train": TRAIN, "validation": VALIDATION, "test": HIDDEN_TEST}


def _source(evidence: list[dict], stages: set[str], documents: dict[int, dict]) -> dict:
    """Источник значения в полях схемы GOLD: файл, редакция, страница, bbox."""
    items = [item for item in evidence if item.get("stage") in stages]
    first = items[0] if items else {}
    meta = documents.get(first.get("document_id"), {})
    return {
        "file_id": first.get("file_id"), "sha256": first.get("sha256"),
        "stage": first.get("stage"), "code": meta.get("document_code"),
        "revision": meta.get("revision"), "approval": meta.get("approval_status"),
        "page": first.get("page"),
        "bbox_polygon": [item.get("bbox") for item in items if item.get("page") ==
                         first.get("page") and item.get("bbox") is not None],
    }


def evidence_group_id(object_id: str, matrix_code: str, evidence: list[dict]) -> str:
    """Объект + параметр + актуальные источники (схема GOLD): один ключ группы."""
    sources = sorted(f"{item.get('file_id')}:{item.get('page')}" for item in evidence)
    raw = "|".join([object_id, matrix_code, *sources])
    return "EG-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def gold_record(run: models.OfficialRun, check: dict, *, status: str, reason: str,
                reason_code: str, user_id: int | None, source_versions: dict) -> dict:
    documents = {item.get("id"): item.get("metadata") or {}
                 for item in run.input_snapshot or []}
    evidence = list(check.get("evidence") or [])
    matrix_code = check.get("parameter_code") or ""
    expected = _source(evidence, {"PD"}, documents)
    actual = _source(evidence, {"RD", "ID"}, documents)
    record = {
        "evidence_group_id": evidence_group_id(run.object_id, matrix_code, evidence),
        "finding_id": check.get("finding_id"), "object_id": run.object_id,
        "matrix_code": matrix_code, "rule_version": source_versions.get("matrix_version"),
        "expected_value": check.get("expected_value"), "actual_value": check.get("actual_value"),
        "approved_change_ref": check.get("approved_change_ref") or "NONE",
        "completeness_status": check.get("completeness_status"),
        "finding_status": status, "review_priority": check.get("priority"),
        "expert_id": user_id, "timestamp": dt.datetime.utcnow().isoformat(),
        "expert_reason_code": reason_code, "expert_comment": reason,
        "matrix_version": source_versions.get("matrix_version"),
        "model_version": source_versions.get("model_version"),
        "input_manifest_hash": source_versions.get("input_manifest_hash"),
    }
    for prefix, source in (("source_expected", expected), ("source_actual", actual)):
        for key, value in source.items():
            record[f"{prefix}_{key}"] = value
    return record


def record_decision(db: Session, run: models.OfficialRun, check: dict, *, status: str,
                    version: int, reason: str, reason_code: str,
                    source_versions: dict, user_id: int | None = None) -> None:
    """Разметка по решению инспектора (вызывается в транзакции решения)."""
    finding_id = check.get("finding_id") or ""
    item = db.query(models.DatasetItem).filter_by(run_id=run.id, finding_id=finding_id).first()
    label = LABELS.get(status)
    if label is None:
        if item is not None:
            item.status = "SUPERSEDED"  # решение изменено на уточнение — из GOLD убрать
    else:
        if item is None:
            item = models.DatasetItem(run_id=run.id, object_id=run.object_id,
                                      finding_id=finding_id, label=label,
                                      decision_version=version)
            db.add(item)
        item.label, item.decision_version = label, version
        item.parameter_code = check.get("parameter_code") or ""
        item.reason, item.reason_code = reason, reason_code
        item.machine_status = check.get("machine_status") or check.get("finding_status") or ""
        item.evidence = list(check.get("evidence") or [])
        item.source_versions = source_versions
        item.record = gold_record(run, check, status=status, reason=reason,
                                  reason_code=reason_code, user_id=user_id,
                                  source_versions=source_versions)
        item.evidence_group_id = item.record["evidence_group_id"]
        item.status, item.curated_by, item.curated_at = "DRAFT", None, None
    if status == "NEGATIVE_VERIFIED":
        db.add(models.RejectionLog(
            run_id=run.id, violation_id=finding_id,
            parameter_code=check.get("parameter_code") or "",
            rejection_reason=reason_code, inspector_comment=reason,
            ai_verdict=(f"{check.get('finding_status') or ''}: "
                        f"{check.get('explanation') or ''}").strip(": "),
            suggested_fix=SUGGESTED_FIX.get(reason_code, SUGGESTED_FIX["OTHER"])))
    if status == "CLARIFICATION_REQUIRED":
        db.add(models.DisputeLog(run_id=run.id, violation_id=finding_id,
                                 inspector_comment=reason,
                                 ai_comment=system_comment(status)))
    else:
        for dispute in db.query(models.DisputeLog).filter_by(
                run_id=run.id, violation_id=finding_id, resolution_status="OPEN"):
            dispute.resolution_status = "RESOLVED"
            dispute.resolved_by = status
            dispute.resolved_at = dt.datetime.utcnow()


def item_dict(item: models.DatasetItem) -> dict:
    return {"id": item.id, "run_id": item.run_id, "object_id": item.object_id,
            "finding_id": item.finding_id, "parameter_code": item.parameter_code,
            "label": item.label, "reason_code": item.reason_code, "reason": item.reason,
            "status": item.status, "evidence": item.evidence,
            "source_versions": item.source_versions,
            "created_at": item.created_at.isoformat()}


def curate(db: Session, item: models.DatasetItem, *, approve: bool, user_id: int) -> None:
    if item.status not in {"DRAFT", "APPROVED", "EXCLUDED"}:
        raise ValueError(f"запись в статусе {item.status} не курируется")
    if approve and not _complete(item.evidence):
        raise ValueError("в GOLD — только записи с полным комплектом доказательств")
    item.status = "APPROVED" if approve else "EXCLUDED"
    item.curated_by, item.curated_at = user_id, dt.datetime.utcnow()


def _complete(evidence: list[dict]) -> bool:
    stages = {item.get("stage") for item in evidence or [] if item.get("bbox") is not None}
    return "PD" in stages and bool(stages & {"RD", "ID"})


def split_of(db: Session, object_id: str) -> str:
    """Набор объекта: назначается один раз, по отпечатку object_id, и не меняется."""
    row = db.get(models.ObjectSplit, object_id)
    if row is None:
        bucket = int(hashlib.sha256(object_id.encode()).hexdigest(), 16) % 100
        split = (TRAIN if bucket < SPLIT_TRAIN_PERCENT else
                 VALIDATION if bucket < SPLIT_TRAIN_PERCENT + SPLIT_VALIDATION_PERCENT
                 else HIDDEN_TEST)
        row = models.ObjectSplit(object_id=object_id, split=split)
        db.add(row)
        db.flush()
    return _LEGACY_SPLITS.get(row.split, row.split)


def _hash(items: list[dict]) -> str:
    payload = json.dumps(sorted(items, key=lambda row: row["id"]), ensure_ascii=False,
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def release(db: Session, *, user_id: int, matrix_version: str) -> models.DatasetVersion:
    """Выпуск dataset_version из одобренных записей финализированных протоколов."""
    rows = (db.query(models.DatasetItem).join(models.OfficialRun)
            .filter(models.DatasetItem.status == "APPROVED",
                    models.OfficialRun.finalized_at.isnot(None))
            .order_by(models.DatasetItem.id).all())
    if not rows:
        raise ValueError("нет одобренных куратором записей из финализированных протоколов")
    splits: dict[str, list[dict]] = {name: [] for name in SPLITS}
    for row in rows:
        splits[split_of(db, row.object_id)].append(
            {"id": row.id, "object_id": row.object_id, "finding_id": row.finding_id,
             "label": row.label, "evidence": row.evidence,
             "source_versions": row.source_versions, "record": row.record})
    number = db.query(models.DatasetVersion).count() + 1
    version = models.DatasetVersion(
        version=f"ds-{number:04d}", matrix_version=matrix_version,
        item_ids=[row.id for row in rows],
        split_hashes={name: _hash(items) for name, items in splits.items()},
        counts={name: dict(Counter(item["label"] for item in items))
                for name, items in splits.items()},
        created_by=user_id)
    db.add(version)
    return version


def gold_rows(db: Session, version: models.DatasetVersion) -> list[dict]:
    """Выпуск набора в полях листа «Схема GOLD»: по строке на evidence_group."""
    rows = (db.query(models.DatasetItem)
            .filter(models.DatasetItem.id.in_(version.item_ids or []))
            .order_by(models.DatasetItem.id).all())
    result = []
    for row in rows:
        # Записи, размеченные до появления полей схемы, получают то, что
        # известно из основных колонок; недостающие поля остаются пустыми.
        base = {"evidence_group_id": row.evidence_group_id or f"EG-legacy-{row.id}",
                "finding_id": row.finding_id, "object_id": row.object_id,
                "matrix_code": row.parameter_code,
                "finding_status": ("CONFIRMED_VIOLATION" if row.label == POSITIVE
                                   else "NEGATIVE_VERIFIED"),
                "expert_reason_code": row.reason_code, "expert_comment": row.reason,
                **{key: (row.source_versions or {}).get(key)
                   for key in ("matrix_version", "model_version")}}
        result.append({**base, **(row.record or {}), "dataset_version": version.version,
                       "split": split_of(db, row.object_id)})
    return result


def acceptance(metrics: dict, per_category: dict, current: models.ModelVersion | None) -> dict:
    """Приёмка модели: пороги 14.3 и сравнение с действующей моделью (9.4)."""
    failures = []
    for key, threshold in ACCEPTANCE.items():
        value = metrics.get(key)
        if value is None or value < threshold:
            failures.append(f"{key} = {value} ниже порога {threshold}")
    fpr = metrics.get("false_positive_rate")
    if fpr is None or fpr > MAX_FALSE_POSITIVE_RATE:
        failures.append(f"false_positive_rate = {fpr} выше порога {MAX_FALSE_POSITIVE_RATE}")
    if current is not None:
        for category, values in (current.per_category_metrics or {}).items():
            before, after = values.get("recall"), (per_category.get(category) or {}).get("recall")
            if before is not None and (after is None or before - after > MAX_RECALL_DROP):
                failures.append(f"Recall категории {category} снизился: {before} → {after}")
        before = current.false_positive_rate
        if before is not None and fpr is not None and fpr - before > MAX_FPR_GROWTH:
            failures.append(f"False Positive Rate вырос: {before} → {fpr}")
    return {"passed": not failures, "failures": failures,
            "compared_with": current.model_version if current else None}


def published(db: Session) -> models.ModelVersion | None:
    return (db.query(models.ModelVersion).filter_by(approval_status="APPROVED")
            .order_by(models.ModelVersion.approved_at.desc()).first())


def weekly_report(db: Session, *, end: dt.datetime | None = None,
                  days: int = 7) -> dict:
    """Статистика отклонений и рекомендации для ML-инженеров (модуль 10)."""
    end = end or dt.datetime.utcnow()
    start = end - dt.timedelta(days=days)
    decisions = (db.query(models.InspectorDecision)
                 .filter(models.InspectorDecision.created_at >= start,
                         models.InspectorDecision.created_at < end).all())
    rejections = (db.query(models.RejectionLog)
                  .filter(models.RejectionLog.created_at >= start,
                          models.RejectionLog.created_at < end).all())
    by_reason = Counter(row.rejection_reason or "OTHER" for row in rejections)
    by_parameter = Counter(row.parameter_code for row in rejections if row.parameter_code)
    confirmed = sum(row.status == "CONFIRMED_VIOLATION" for row in decisions)
    rejected = sum(row.status == "NEGATIVE_VERIFIED" for row in decisions)
    recommendations = [f"{reason} ({count}): {SUGGESTED_FIX.get(reason, SUGGESTED_FIX['OTHER'])}"
                       for reason, count in by_reason.most_common()]
    recommendations += [f"Параметр {code}: отклонено кандидатов — {count}; проверить правило "
                        "сравнения и порог в матрице"
                        for code, count in by_parameter.most_common() if count > 1]
    drafts = db.query(models.DatasetItem).filter_by(status="DRAFT").count()
    if drafts:
        recommendations.append(f"Записей в черновике набора, ожидающих куратора: {drafts}")
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "decisions": {"total": len(decisions), "confirmed": confirmed, "rejected": rejected,
                      "clarification": len(decisions) - confirmed - rejected},
        "rejection_share": round(rejected / (confirmed + rejected), 4)
        if confirmed + rejected else None,
        "rejections_by_reason": dict(by_reason),
        "rejections_by_parameter": dict(by_parameter.most_common()),
        "open_disputes": db.query(models.DisputeLog).filter_by(resolution_status="OPEN").count(),
        "dataset_drafts": drafts,
        "published_model": (published(db).model_version if published(db) else None),
        "recommendations": recommendations,
    }
