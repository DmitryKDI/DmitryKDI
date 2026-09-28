"""Свободный поиск гипотез в процессе проверки (ТЗ 7, модуль 5; 9.5).

Собирает входы для `suspicions.discover` — правила и нормы из базы, названия
помещений из разбора актуальных редакций, значения параметров на других
объектах — и сохраняет найденное в таблицу Suspicions. Сбой свободного
поиска не роняет проверку по матрице: он виден в результате отдельным
состоянием, а не превращается в «гипотез нет» (Г.10).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from . import facts_store, models, suspicions


def _rules(db: Session) -> list[dict]:
    rows = db.query(models.LogicalRule).filter_by(is_active=True).all()
    return [{"id": row.id, "rule_name": row.rule_name, "condition": row.condition,
             "expected": row.expected, "normative_base": row.normative_base,
             "review_priority": row.review_priority} for row in rows]


def _norms(db: Session) -> list[dict]:
    rows = db.query(models.NormativeBase).filter_by(is_active=True).all()
    return [{"id": row.id, "document_number": row.document_number, "section": row.section,
             "parameter_name": row.parameter_name, "min_value": row.min_value,
             "max_value": row.max_value, "effective_from": row.effective_from,
             "effective_to": row.effective_to} for row in rows]


def _history(db: Session, object_id: str) -> dict[str, list[float]]:
    """Значения параметров на ДРУГИХ объектах: последний завершённый прогон объекта."""
    latest: dict[str, models.OfficialRun] = {}
    for run in (db.query(models.OfficialRun)
                .filter(models.OfficialRun.object_id != object_id,
                        models.OfficialRun.status == "completed")
                .order_by(models.OfficialRun.id).all()):
        latest[run.object_id] = run
    values: dict[str, list[float]] = {}
    for run in latest.values():
        for check in (run.result or {}).get("checks") or []:
            number = suspicions.number_of(check.get("actual_value"))
            if number is not None and check.get("technical_status") == "completed":
                values.setdefault(check["parameter_code"], []).append(number)
    return values


def _rooms(documents: list, selected: dict[str, list[int]]) -> dict[str, list[dict]]:
    by_id = {document.id: document for document in documents}
    rooms: dict[str, list[dict]] = {}
    for stage, ids in selected.items():
        for document_id in ids:
            document = by_id.get(document_id)
            if document is None:
                continue
            facts = facts_store.facts_for(document.file_path, document.name,
                                          digest=document.digest)
            rooms.setdefault(stage, []).extend(
                {"key": item["key"], "name": item.get("name") or "",
                 "page": item.get("page"), "document_id": document_id}
                for item in facts.room_facts if item.get("key") and item.get("name"))
    return rooms


def _codes(documents: list) -> dict[int, str]:
    return {document.id: str((document.source_metadata or {}).get("document_code") or "")
            for document in documents}


def _evidence(item: dict, checks: dict[str, dict]) -> list[dict]:
    check = checks.get(item.get("parameter_code") or "")
    return list((check or {}).get("evidence") or [])


def run(db: Session, run_row: models.OfficialRun, documents: list, result: dict) -> dict:
    """Найти, сохранить и вернуть гипотезы процесса."""
    try:
        selected = (result.get("document_selection") or {}).get("selected") or {}
        checks = result.get("checks") or []
        found = suspicions.discover(
            checks, rules=_rules(db), norms=_norms(db), rooms=_rooms(documents, selected),
            history=_history(db, run_row.object_id), document_codes=_codes(documents),
            today=dt.date.today())
    except Exception as exc:  # noqa: BLE001 — сбой виден в результате, не как «гипотез нет»
        return {"status": "error", "reason": f"свободный поиск не выполнен: {exc}",
                "items": []}
    by_code = {check.get("parameter_code"): check for check in checks}
    db.query(models.Suspicion).filter_by(run_id=run_row.id).delete()
    items = []
    for item in found:
        row = models.Suspicion(
            object_id=run_row.object_id, run_id=run_row.id,
            discovery_method=item["discovery_method"], confidence=item["confidence"],
            description=item["description"], pd_reference=item["pd_reference"],
            rd_reference=item["rd_reference"], review_priority=item["review_priority"],
            normative_base=item["normative_base"],
            parameter_code=item.get("parameter_code") or "",
            evidence=_evidence(item, by_code))
        db.add(row)
        db.flush()
        items.append(suspicion_dict(row))
    return {"status": "completed", "reason": "", "items": items}


def suspicion_dict(row: models.Suspicion) -> dict:
    """Подозрение в структуре ТЗ 9.5."""
    return {
        "suspicion_id": row.id, "object_id": row.object_id,
        "discovery_method": row.discovery_method, "confidence": row.confidence,
        "description": row.description, "pd_reference": row.pd_reference,
        "rd_reference": row.rd_reference, "review_priority": row.review_priority,
        "normative_base": row.normative_base, "parameter_code": row.parameter_code or None,
        "evidence": row.evidence or [], "finding_status": row.finding_status,
        "inspector_status": row.inspector_status,
        "inspector_comment": row.inspector_comment,
    }


def has_coordinates(evidence: list[dict]) -> bool:
    """Для перевода в CANDIDATE: источники с координатами в ПД и в РД/ИД (ТЗ 9.5)."""
    stages = {item.get("stage") for item in evidence
              if item.get("bbox") is not None and item.get("document_id") is not None
              and item.get("page") is not None}
    return "PD" in stages and bool(stages & {"RD", "ID"})
