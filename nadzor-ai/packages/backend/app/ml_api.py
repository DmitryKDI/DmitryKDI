"""Набор данных, версии моделей и еженедельный отчёт (ТЗ 7, модули 4 и 10; 9.4).

Куратор данных (роль ML-инженера) одобряет записи черновика, выпускает
dataset_version и регистрирует результаты обучения. Публикацию модели
подписывает администратор — только после приёмки; откат сохраняется.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import auth, feedback, models, parameter_catalog
from .db import get_session

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])
ML = auth.require("ml_engineer")
READ = Depends(auth.require("ml_engineer", "supervisor"))


class CurateInput(BaseModel):
    approve: bool


class ModelInput(BaseModel):
    model_version: str
    dataset_version: str
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)
    false_positive_rate: float = Field(ge=0, le=1)
    per_category_metrics: dict = {}
    training_params: dict = {}
    code_ref: str = ""


def _model_dict(row: models.ModelVersion) -> dict:
    return {"id": row.id, "model_version": row.model_version,
            "dataset_version": row.dataset_version, "matrix_version": row.matrix_version,
            "split_hashes": row.split_hashes, "precision": row.precision,
            "recall": row.recall, "f1": row.f1, "false_positive_rate": row.false_positive_rate,
            "per_category_metrics": row.per_category_metrics,
            "training_params": row.training_params, "code_ref": row.code_ref,
            "previous_model": row.previous_model, "acceptance": row.acceptance,
            "approval_status": row.approval_status, "approved_by": row.approved_by,
            "approved_at": row.approved_at.isoformat() if row.approved_at else None,
            "created_at": row.created_at.isoformat()}


def _version_dict(row: models.DatasetVersion) -> dict:
    return {"version": row.version, "matrix_version": row.matrix_version,
            "items": len(row.item_ids or []), "split_hashes": row.split_hashes,
            "counts": row.counts, "created_at": row.created_at.isoformat()}


@router.get("/dataset/items", summary="Записи набора данных", dependencies=[READ])
def dataset_items(status: str | None = None, db: Session = Depends(get_session)):
    query = db.query(models.DatasetItem)
    if status:
        query = query.filter_by(status=status)
    return [feedback.item_dict(row) for row in query.order_by(models.DatasetItem.id).all()]


@router.post("/dataset/items/{item_id}/curate", summary="Решение куратора по записи")
def curate(item_id: int, body: CurateInput, db: Session = Depends(get_session),
           user: auth.Principal = Depends(ML)):
    row = db.get(models.DatasetItem, item_id)
    if row is None:
        raise HTTPException(404, "запись не найдена")
    try:
        feedback.curate(db, row, approve=body.approve, user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return feedback.item_dict(row)


@router.post("/dataset/versions", summary="Выпустить dataset_version")
def release(db: Session = Depends(get_session), user: auth.Principal = Depends(ML)):
    try:
        row = feedback.release(db, user_id=user.user_id,
                               matrix_version=parameter_catalog.current_matrix_version())
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return _version_dict(row)


@router.get("/dataset/versions", summary="Выпуски набора данных", dependencies=[READ])
def versions(db: Session = Depends(get_session)):
    rows = db.query(models.DatasetVersion).order_by(models.DatasetVersion.id).all()
    return [_version_dict(row) for row in rows]


@router.get("/dataset/versions/{version}/evaluate", dependencies=[READ],
            summary="Метрики текущих машинных результатов на выборке выпуска")
def evaluate(version: str, split: str = Query("test", pattern="^(train|validation|test)$"),
             db: Session = Depends(get_session)):
    """Precision / Recall / F1 / FPR по evidence_group (ТЗ 14.3).

    Машина «предсказала нарушение», если её статус находки был CANDIDATE.
    Метрики — по разделам и в целом, с размером выборки: без него точечная
    оценка на десятке записей выглядела бы весомее, чем есть.
    """
    row = db.query(models.DatasetVersion).filter_by(version=version).first()
    if row is None:
        raise HTTPException(404, "выпуск не найден")
    items = [item for item in db.query(models.DatasetItem)
             .filter(models.DatasetItem.id.in_(row.item_ids or [])).all()
             if feedback.split_of(db, item.object_id) == split]
    sections = {item["code"]: item["section"]
                for item in parameter_catalog.list_parameters(include_inactive=True)}
    groups: dict[str, list] = {"all": items}
    for item in items:
        groups.setdefault(sections.get(item.parameter_code, "без раздела"), []).append(item)
    return {"dataset_version": version, "split": split,
            "metrics": {name: _metrics(group) for name, group in groups.items()}}


def _metrics(items: list[models.DatasetItem]) -> dict:
    tp = sum(i.label == feedback.POSITIVE and i.machine_status == "CANDIDATE" for i in items)
    fn = sum(i.label == feedback.POSITIVE and i.machine_status != "CANDIDATE" for i in items)
    fp = sum(i.label == feedback.NEGATIVE and i.machine_status == "CANDIDATE" for i in items)
    tn = sum(i.label == feedback.NEGATIVE and i.machine_status != "CANDIDATE" for i in items)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    return {"size": len(items), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
            "false_positive_rate": fp / (fp + tn) if fp + tn else None}


@router.get("/rejections", summary="Лог отклонений", dependencies=[READ])
def rejections(db: Session = Depends(get_session)):
    rows = db.query(models.RejectionLog).order_by(models.RejectionLog.id.desc()).all()
    return [{"id": row.id, "run_id": row.run_id, "violation_id": row.violation_id,
             "parameter_code": row.parameter_code, "rejection_reason": row.rejection_reason,
             "inspector_comment": row.inspector_comment, "ai_verdict": row.ai_verdict,
             "suggested_fix": row.suggested_fix, "retraining_status": row.retraining_status,
             "created_at": row.created_at.isoformat()} for row in rows]


@router.get("/disputes", summary="Спорные случаи", dependencies=[READ])
def disputes(db: Session = Depends(get_session)):
    rows = db.query(models.DisputeLog).order_by(models.DisputeLog.id.desc()).all()
    return [{"id": row.id, "run_id": row.run_id, "violation_id": row.violation_id,
             "inspector_comment": row.inspector_comment, "ai_comment": row.ai_comment,
             "resolution_status": row.resolution_status, "resolved_by": row.resolved_by,
             "created_at": row.created_at.isoformat()} for row in rows]


@router.get("/models", summary="Итерации дообучения", dependencies=[READ])
def model_list(db: Session = Depends(get_session)):
    current = feedback.published(db)
    return {"published": current.model_version if current else None,
            "models": [_model_dict(row) for row in db.query(models.ModelVersion)
                       .order_by(models.ModelVersion.id).all()]}


@router.post("/models", summary="Зарегистрировать результат обучения")
def register_model(body: ModelInput, db: Session = Depends(get_session),
                   _: auth.Principal = Depends(ML)):
    dataset = db.query(models.DatasetVersion).filter_by(version=body.dataset_version).first()
    if dataset is None:
        raise HTTPException(422, "обучение допускается только на выпущенном dataset_version")
    if db.query(models.ModelVersion).filter_by(model_version=body.model_version).first():
        raise HTTPException(409, "такая версия модели уже зарегистрирована")
    current = feedback.published(db)
    metrics = body.model_dump(include={"precision", "recall", "f1", "false_positive_rate"})
    row = models.ModelVersion(
        model_version=body.model_version, dataset_version=body.dataset_version,
        matrix_version=dataset.matrix_version, split_hashes=dataset.split_hashes,
        per_category_metrics=body.per_category_metrics, training_params=body.training_params,
        code_ref=body.code_ref, previous_model=current.model_version if current else "",
        acceptance=feedback.acceptance(metrics, body.per_category_metrics, current),
        **metrics)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _model_dict(row)


@router.post("/models/{model_id}/approve", summary="Подписать публикацию модели")
def approve_model(model_id: int, db: Session = Depends(get_session),
                  user: auth.Principal = Depends(auth.require("admin"))):
    row = db.get(models.ModelVersion, model_id)
    if row is None:
        raise HTTPException(404, "модель не найдена")
    if row.approval_status != "PENDING":
        raise HTTPException(409, f"модель в статусе {row.approval_status}")
    if not (row.acceptance or {}).get("passed"):
        raise HTTPException(409, "модель не прошла приёмку: "
                                 + "; ".join((row.acceptance or {}).get("failures") or []))
    row.approval_status, row.approved_by = "APPROVED", user.display
    row.approved_at = dt.datetime.utcnow()
    db.commit()
    return _model_dict(row)


@router.post("/models/{model_id}/reject", summary="Отклонить модель")
def reject_model(model_id: int, db: Session = Depends(get_session),
                 user: auth.Principal = Depends(auth.require("admin"))):
    row = db.get(models.ModelVersion, model_id)
    if row is None or row.approval_status != "PENDING":
        raise HTTPException(409, "отклонить можно только модель, ожидающую решения")
    row.approval_status, row.approved_by = "REJECTED", user.display
    db.commit()
    return _model_dict(row)


@router.post("/models/{model_id}/rollback", summary="Откатить опубликованную модель")
def rollback_model(model_id: int, db: Session = Depends(get_session),
                   user: auth.Principal = Depends(auth.require("admin"))):
    row = db.get(models.ModelVersion, model_id)
    if row is None or row.approval_status != "APPROVED":
        raise HTTPException(409, "откатить можно только опубликованную модель")
    row.approval_status = "ROLLED_BACK"
    db.commit()
    current = feedback.published(db)
    return {"rolled_back": row.model_version,
            "published": current.model_version if current else None}


@router.get("/report", summary="Отчёт по дообучению за период", dependencies=[READ])
def report(days: int = Query(7, ge=1, le=366), db: Session = Depends(get_session)):
    payload = feedback.weekly_report(db, days=days)
    db.add(models.WeeklyReport(period_start=dt.datetime.fromisoformat(payload["period_start"]),
                               period_end=dt.datetime.fromisoformat(payload["period_end"]),
                               payload=payload))
    db.commit()
    return payload


@router.get("/reports", summary="Сохранённые отчёты", dependencies=[READ])
def reports(db: Session = Depends(get_session)):
    rows = db.query(models.WeeklyReport).order_by(models.WeeklyReport.id.desc()).limit(52)
    return [row.payload | {"id": row.id} for row in rows]
