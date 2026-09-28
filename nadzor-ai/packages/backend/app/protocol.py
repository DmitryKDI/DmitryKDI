"""Протокол проверки в терминах ТЗ: сценарий, статусы, пять таблиц, версии.

Конвейер (`official_pipeline`) отвечает на вопрос «что показали документы по
каждому параметру». Этот модуль отвечает на другой: как это выглядит в
протоколе, который принимает инспектор и передаёт дальше. Разделение
намеренное: правила ТЗ о статусах и жизненном цикле протокола меняются
независимо от механики сравнения, и смешивать их значило бы править
сравнение ради формулировки статуса.

Всё здесь — чистые функции над уже посчитанным результатом и решениями
инспектора. Ничего не вызывает модель и не читает файлы.
"""
from __future__ import annotations

import hashlib
import os
import re

STAGES = ("PD", "RD", "ID")

# Сценарий загрузки (ТЗ 9.2, п.2).
SCENARIO_FULL = "FULL"
SCENARIO_PD_RD = "PD_RD_ONLY"
SCENARIO_PD_ID = "PD_ID_ONLY"
SCENARIO_RD_ID = "RD_ID_ONLY"
SCENARIO_SINGLE = "SINGLE_ONLY"
SCENARIO_PARTIAL = "PARTIALLY_LOADED"
SCENARIO_NONE = "NO_DOCUMENTS"

# Статусы процесса проверки (ТЗ 9.1). ERROR и CANCELLED в таблице ТЗ нет,
# но сбой и остановка — реальные состояния: выдать их за PENDING или READY
# значило бы показать непроверенное как готовое (Г.10).
PENDING, PARSING, READY = "PENDING", "PARSING", "READY"
VERIFYING, COMPLETED, FINALIZED = "VERIFYING", "COMPLETED", "FINALIZED"
ERROR, CANCELLED = "ERROR", "CANCELLED"

# Статусы верификации (ТЗ 9.3).
VERIFICATION_PENDING = "PENDING"
VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
PROTOCOL_FINALIZED = "PROTOCOL_FINALIZED"

# Причины отклонения кандидата инспектором (ТЗ 9.3, п.2): кодированная
# причина обязательна, иначе отрицательный пример нельзя использовать для
# разбора ошибок модели.
REASON_CODES = {
    "WRONG_REVISION": "актуальная редакция выбрана неверно",
    "APPROVED_CHANGE": "есть согласованное изменение",
    "OCR_ERROR": "ошибка распознавания",
    "BINDING_ERROR": "ошибка привязки доказательства",
    "NOT_APPLICABLE": "параметр неприменим",
    "NO_DIFFERENCE": "расхождения нет",
    "OTHER": "иное (см. комментарий)",
}

_UPLOAD_ALLOWED = {PENDING, READY, VERIFYING, COMPLETED}
_VERIFY_ALLOWED = {READY, VERIFYING}


def upload_statuses(stage_counts: dict[str, int], problems: dict[str, str]) -> dict[str, str]:
    """PD_UPLOADED / PD_PARTIAL / PD_MISSING и так же для RD, ID.

    Частичная загрузка — стадия есть, но часть её томов не даёт однозначной
    актуальной редакции: сравнивать по ней можно не всё.
    """
    statuses = {}
    for stage in STAGES:
        if not stage_counts.get(stage):
            statuses[stage] = f"{stage}_MISSING"
        elif problems.get(stage):
            statuses[stage] = f"{stage}_PARTIAL"
        else:
            statuses[stage] = f"{stage}_UPLOADED"
    return statuses


def scenario(statuses: dict[str, str]) -> str:
    present = {stage for stage in STAGES if not statuses[stage].endswith("_MISSING")}
    if any(statuses[stage].endswith("_PARTIAL") for stage in present):
        return SCENARIO_PARTIAL
    if present == set(STAGES):
        return SCENARIO_FULL
    pairs = {frozenset({"PD", "RD"}): SCENARIO_PD_RD, frozenset({"PD", "ID"}): SCENARIO_PD_ID,
             frozenset({"RD", "ID"}): SCENARIO_RD_ID}
    if frozenset(present) in pairs:
        return pairs[frozenset(present)]
    return SCENARIO_SINGLE if present else SCENARIO_NONE


def _all_findings(result: dict | None) -> list[dict]:
    if not result:
        return []
    return list(result.get("checks") or []) + list(
        (result.get("graphic_analysis") or {}).get("candidates") or [])


def _free_items(result: dict | None) -> list[dict]:
    return list(((result or {}).get("free_search") or {}).get("items") or [])


def pending_candidates(result: dict | None) -> list[str]:
    """Кандидаты без решения инспектора — финализация без них запрещена.

    Гипотеза свободного поиска, переведённая в CANDIDATE, — такой же кандидат.
    """
    return [item["finding_id"] for item in _all_findings(result)
            if item.get("finding_status") == "CANDIDATE"] + [
        f"S-{item.get('suspicion_id')}" for item in _free_items(result)
        if item.get("finding_status") == "CANDIDATE"]


def process_status(run_status: str, finalized: bool, result: dict | None,
                   decisions: int) -> str:
    if run_status == "queued":
        return PENDING
    if run_status == "running":
        return PARSING
    if run_status == "error":
        return ERROR
    if run_status == "cancelled":
        return CANCELLED
    if finalized:
        return FINALIZED
    if pending_candidates(result):
        return VERIFYING if decisions else READY
    return COMPLETED


def verification_status(finalized: bool, result: dict | None) -> str:
    if finalized:
        return PROTOCOL_FINALIZED
    return VERIFICATION_PENDING if pending_candidates(result) else VERIFICATION_COMPLETED


def can_upload(status: str) -> bool:
    return status in _UPLOAD_ALLOWED


def can_verify(status: str) -> bool:
    return status in _VERIFY_ALLOWED


def manifest_hash(snapshot: list[dict]) -> str:
    """Отпечаток входа: какие файлы, с какими реквизитами вошли в проверку.

    Два протокола с одинаковым отпечатком построены по одному и тому же
    комплекту, и расхождение между ними — свойство модели, а не данных.
    """
    rows = []
    for item in snapshot:
        meta = item.get("metadata") or {}
        rows.append("|".join(str(value) for value in (
            item.get("id"), item.get("digest"), meta.get("stage"), meta.get("document_code"),
            meta.get("revision"), meta.get("approval_status"))))
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def versions(model_version: str, matrix_version: str, snapshot: list[dict]) -> dict:
    # Дообучения весов не было: модель используется как есть. Версия набора
    # данных задаётся при развёртывании, когда она появится (ТЗ 9.4).
    return {
        "matrix_version": matrix_version,
        "model_version": model_version,
        "dataset_version": os.environ.get("NADZOR_DATASET_VERSION", "none"),
        "input_manifest_hash": manifest_hash(snapshot),
    }


def numeric_delta(expected: object, actual: object) -> float | None:
    """Разница «факт − проект», если оба значения числовые; иначе None.

    Нечисловые значения (класс, марка, конфигурация) сравниваются словами:
    придумывать им число значило бы выдавать неизмеримое за измеренное.
    """
    def number(value: object) -> float | None:
        match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or "").replace(" ", ""))
        return float(match.group().replace(",", ".")) if match else None

    left, right = number(expected), number(actual)
    return None if left is None or right is None else round(right - left, 6)


def _source(evidence: dict, documents: dict[int, dict]) -> dict:
    meta = documents.get(evidence.get("document_id"), {})
    return {
        "file_id": evidence.get("file_id"), "sha256": evidence.get("sha256"),
        "stage": evidence.get("stage"), "role": evidence.get("role"),
        "document_code": meta.get("document_code"), "revision": meta.get("revision"),
        "approval_status": meta.get("approval_status"), "page": evidence.get("page"),
        "bbox_polygon": evidence.get("bbox"), "quote": evidence.get("quote"),
    }


def evidence_card(item: dict, documents: dict[int, dict]) -> dict:
    """Карточка доказательства (ТЗ 9.2, п.4) — всё, что нужно для решения."""
    history = item.get("review_history") or []
    last = history[-1] if history else {}
    return {
        "finding_id": item.get("finding_id"),
        "parameter_code": item.get("parameter_code"),
        "parameter_name": item.get("parameter_name"),
        "finding_status": item.get("finding_status"),
        "completeness_status": item.get("completeness_status"),
        "expected_value": item.get("expected_value"),
        "actual_value": item.get("actual_value"),
        "delta": item.get("delta"),
        "approved_change_ref": item.get("approved_change_ref") or "NONE",
        "sources": [_source(evidence, documents) for evidence in item.get("evidence") or []],
        "rationale": item.get("explanation"),
        "risk_level": item.get("priority"),
        "confidence": item.get("confidence"),
        "inspector_decision": last.get("status"),
        "inspector_reason_code": last.get("reason_code"),
        "inspector_comment": last.get("reason"),
        "inspector": last.get("author"),
    }


def suspicion_card(item: dict, documents: dict[int, dict]) -> dict:
    """Гипотеза свободного поиска в структуре ТЗ 9.5 и в форме карточки протокола."""
    return {
        "finding_id": f"S-{item.get('suspicion_id')}",
        "suspicion_id": item.get("suspicion_id"),
        "discovery_method": item.get("discovery_method"),
        "parameter_code": item.get("parameter_code"),
        "parameter_name": item.get("description"),
        "description": item.get("description"),
        "finding_status": item.get("finding_status"),
        "pd_reference": item.get("pd_reference"),
        "rd_reference": item.get("rd_reference"),
        "normative_base": item.get("normative_base"),
        "review_priority": item.get("review_priority"),
        "confidence": item.get("confidence"),
        "sources": [_source(evidence, documents) for evidence in item.get("evidence") or []],
        "inspector_decision": item.get("inspector_status"),
        "inspector_comment": item.get("inspector_comment"),
    }


def build(result: dict | None, snapshot: list[dict], *, run_status: str,
          finalized: bool, decisions: int, model_version: str) -> dict:
    """Протокол целиком: загрузка, сценарий, пять таблиц, версии."""
    documents = {item.get("id"): item.get("metadata") or {} for item in snapshot}
    stage_counts: dict[str, int] = {}
    for meta in documents.values():
        stage_counts[meta.get("stage")] = stage_counts.get(meta.get("stage"), 0) + 1
    problems = ((result or {}).get("document_selection") or {}).get("problems") or {}
    statuses = upload_statuses(stage_counts, problems)
    findings = _all_findings(result)

    def cards(status: str) -> list[dict]:
        return [evidence_card(item, documents) for item in findings
                if item.get("finding_status") == status]

    def free(status: str) -> list[dict]:
        return [suspicion_card(item, documents) for item in _free_items(result)
                if item.get("finding_status") == status]

    status = process_status(run_status, finalized, result, decisions)
    return {
        "status": status,
        "verification_status": verification_status(finalized, result),
        "upload_status": statuses,
        "scenario": scenario(statuses),
        "versions": versions(model_version, (result or {}).get("matrix_version", ""), snapshot),
        "tables": {
            "completeness": [{
                "finding_id": item.get("finding_id"),
                "parameter_code": item.get("parameter_code"),
                "parameter_name": item.get("parameter_name"),
                "completeness_status": item.get("completeness_status"),
                "technical_status": item.get("technical_status"),
                "reason": item.get("explanation"),
            } for item in findings],
            "candidates": cards("CANDIDATE") + free("CANDIDATE"),
            "confirmed_violations": cards("CONFIRMED_VIOLATION") + free("CONFIRMED_VIOLATION"),
            "negative_verified": cards("NEGATIVE_VERIFIED") + free("NEGATIVE_VERIFIED"),
            # Графические находки без полного набора доказательств и гипотезы
            # свободного поиска (ТЗ 9.5) — одна таблица, ни то ни другое не нарушение.
            "suspicions": cards("SUSPICION") + free("SUSPICION"),
        },
        "free_search": {key: value for key, value in
                        ((result or {}).get("free_search") or {"status": "not_run"}).items()
                        if key != "items"},
        # Покрываемость распознавания по документам: доля и номера листов,
        # которые машина не прочитала или прочитала плохо.
        "ocr_quality": (result or {}).get("ocr_quality") or {},
        "missing_evidence": [item.get("finding_id") for item in findings
                             if item.get("completeness_status") == "MISSING_EVIDENCE"],
        "pending_candidates": pending_candidates(result),
    }
