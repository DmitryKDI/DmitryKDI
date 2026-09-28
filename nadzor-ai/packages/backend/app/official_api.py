"""HTTP-контракт официальной проверки ПД/РД/ИД по матрице 1.1."""
from __future__ import annotations

import copy
import csv
import datetime as dt
import io
import json
import threading
from types import SimpleNamespace

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from . import auth, feedback, free_search, models, protocol
from .db import SessionLocal, get_session
from .llm import LlmConfig, local_config
from .official_pipeline import (
    parameters_with_new_data,
    run_official_analysis,
    select_current_documents,
)
from .parameter_catalog import current_matrix_version, list_parameters

router = APIRouter(prefix="/official", tags=["official"])
STAGES = {"PD", "RD", "ID"}
APPROVAL_STATUSES = {"DRAFT", "APPROVED", "FOR_CONSTRUCTION", "SUPERSEDED", "CANCELLED"}
DECISION_STATUSES = {
    "CANDIDATE", "CONFIRMED_VIOLATION", "NEGATIVE_VERIFIED", "CLARIFICATION_REQUIRED",
}


class MetadataInput(BaseModel):
    object_id: str
    stage: str
    document_code: str
    revision: str
    approval_status: str
    approval_date: dt.date | None = None
    predecessor_id: int | None = None
    signature_status: str | None = None
    sheet_page_range: str | None = None
    # Перечень ИД, ред. 1.1: раздел/марка комплекта и идентификатор файла из
    # реестра. file_id — ключ файла в доказательствах и эталонной разметке.
    discipline: str | None = None
    file_id: str | None = None

    @field_validator("object_id", "document_code", "revision")
    @classmethod
    def required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("поле обязательно")
        return value

    @field_validator("stage")
    @classmethod
    def valid_stage(cls, value: str) -> str:
        if value not in STAGES:
            raise ValueError("стадия должна быть PD, RD или ID")
        return value

    @field_validator("approval_status")
    @classmethod
    def valid_approval(cls, value: str) -> str:
        if value not in APPROVAL_STATUSES:
            raise ValueError("неизвестный статус утверждения")
        return value


class RunInput(BaseModel):
    object_id: str
    document_ids: list[int]
    # Не влияет на проверку: ТЗ 9.2 требует все активные параметры матрицы.
    # Поле оставлено, чтобы прежние клиенты не получали ошибку.
    include_medium: bool = False


class DecisionInput(BaseModel):
    finding_id: str
    status: str
    reason: str
    # Автор берётся из учётной записи; поле оставлено для совместимости
    # клиентов и в решение не записывается.
    author: str = ""
    expected_version: int
    # Кодированная причина (ТЗ 9.3): обязательна при отклонении кандидата.
    reason_code: str = ""


class SuspicionReview(BaseModel):
    # promote — перевести в CANDIDATE (нужны источники с координатами в ПД и
    # в РД/ИД); dismiss — отклонить гипотезу; confirm / reject — решение по
    # уже переведённому кандидату (ТЗ 9.5).
    action: str
    comment: str = ""
    reason_code: str = ""
    evidence: list[dict] | None = None


class FinalizeInput(BaseModel):
    # Автор — пользователь, выполнивший вход; поле для совместимости.
    author: str = ""


class UnfinalizeInput(BaseModel):
    reason: str
    # Роль берётся из учётной записи (ТЗ 9.3: администратор или супервизор).
    author: str = ""
    role: str = ""


# Сколько раз повторить проверку, упавшую с исключением (ТЗ 9.1: таймаут —
# до двух повторов). Результат с честным not_run не повторяется: это не сбой.
EXECUTE_RETRIES = 2


def _document_dict(document: models.Document) -> dict:
    return {
        "id": document.id, "name": document.name, "pages": document.pages,
        "status": document.status, "digest": document.digest,
        "metadata": copy.deepcopy(document.source_metadata or {}),
    }


def _settings_config(db: Session) -> LlmConfig:
    """Модель локальная и задаётся при развёртывании (см. llm.local_config)."""
    return local_config()


def _version(db: Session, run_id: int) -> int:
    rows = db.query(models.InspectorDecision).filter_by(run_id=run_id).all()
    return max((row.version for row in rows), default=0)


def _result_checks(result: dict | None) -> list[dict]:
    if not result:
        return []
    checks = list(result.get("checks") or [])
    checks.extend((result.get("graphic_analysis") or {}).get("candidates") or [])
    return checks


def _run_dict(db: Session, run: models.OfficialRun) -> dict:
    result = copy.deepcopy(run.result)
    decisions = (db.query(models.InspectorDecision).filter_by(run_id=run.id)
                 .order_by(models.InspectorDecision.version).all())
    if result:
        checks = {item["finding_id"]: item for item in _result_checks(result)}
        for decision in decisions:
            check = checks.get(decision.finding_id)
            if check is None or decision.version <= check.get("decisions_after_version", 0):
                continue
            history = check.setdefault("review_history", [])
            history.append({
                "status": decision.status, "author": decision.author,
                "user_id": decision.user_id,
                "reason": decision.reason, "reason_code": decision.reason_code,
                "created_at": decision.created_at.isoformat(),
                "version": decision.version,
            })
            check["finding_status"] = decision.status
    built = protocol.build(
        result, run.input_snapshot or [], run_status=run.status,
        finalized=run.finalized_at is not None, decisions=len(decisions),
        model_version=run.model_version or "",
        dataset_version=published.dataset_version if (published := feedback.published(db))
        else None)
    return {
        # process_id — идентификатор процесса в контракте pull-модели: клиент
        # запускает проверку, получает process_id и опрашивает статус по нему.
        # id оставлен для интерфейса; значения совпадают всегда.
        "process_id": run.id,
        "id": run.id, "object_id": run.object_id, "status": run.status, "stage": run.stage,
        # Статус процесса в словаре ТЗ (PENDING … FINALIZED); status выше —
        # внутреннее состояние фоновой задачи, по нему работает интерфейс.
        "process_status": built["status"],
        "verification_status": built["verification_status"],
        "finalized_at": run.finalized_at.isoformat() if run.finalized_at else None,
        "finalized_by": run.finalized_by or None,
        "sync_status": run.sync_status,
        "sync_attempts": run.sync_attempts or 0,
        "sync_next_at": run.sync_next_at.isoformat() if run.sync_next_at else None,
        # Документы после финализации: уведомление инспектору (ТЗ 9.6).
        "pending_documents": run.pending_documents or [],
        "protocol_version": len(run.protocol_history or []) + 1,
        "completed": run.completed, "total": run.total, "result": result,
        "protocol": built,
        "error": run.error, "version": max((row.version for row in decisions), default=0),
        # Системный комментарий к последнему решению (ТЗ 9.4).
        "system_comment": feedback.system_comment(decisions[-1].status,
                                                  decisions[-1].reason_code)
        if decisions else "",
    }


@router.get("/parameters")
def parameters():
    return {"matrix_version": current_matrix_version(), "parameters": list_parameters()}


@router.get("/documents")
def documents(db: Session = Depends(get_session)):
    rows = db.query(models.Document).order_by(models.Document.uploaded_at.desc()).all()
    return [_document_dict(row) for row in rows
            if (row.source_metadata or {}).get("stage") in STAGES]


@router.put("/documents/{document_id}/metadata",
            dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def save_metadata(document_id: int, body: MetadataInput, db: Session = Depends(get_session)):
    document = db.get(models.Document, document_id)
    if document is None:
        raise HTTPException(404, "документ не найден")
    if body.approval_status in {"APPROVED", "FOR_CONSTRUCTION"} and body.approval_date is None:
        raise HTTPException(422, "для утверждённой редакции нужна дата утверждения")
    if body.predecessor_id == document_id:
        raise HTTPException(422, "редакция не может заменять саму себя")
    if body.predecessor_id is not None:
        predecessor = db.get(models.Document, body.predecessor_id)
        if predecessor is None:
            raise HTTPException(422, "заменяемая редакция не найдена")
        predecessor_meta = predecessor.source_metadata or {}
        if predecessor_meta.get("object_id") != body.object_id:
            raise HTTPException(422, "редакции относятся к разным объектам")
        if predecessor_meta.get("stage") != body.stage:
            raise HTTPException(422, "редакции относятся к разным стадиям")
        if predecessor_meta.get("document_code") != body.document_code:
            raise HTTPException(422, "редакции имеют разные шифры документа")
        seen = {document_id}
        current = predecessor
        while current is not None:
            if current.id in seen:
                raise HTTPException(422, "цепочка редакций содержит цикл")
            seen.add(current.id)
            parent_id = (current.source_metadata or {}).get("predecessor_id")
            current = db.get(models.Document, parent_id) if parent_id is not None else None
    metadata = body.model_dump(mode="json")
    document.source_metadata = metadata
    document.metadata_version += 1
    document.side = "before" if body.stage == "PD" else "after"
    db.add(models.DocumentMetadataEvent(
        document_id=document.id, version=document.metadata_version,
        snapshot=copy.deepcopy(metadata),
    ))
    db.commit()
    db.refresh(document)
    return _document_dict(document)


def _selection(documents) -> dict[str, list[int]]:
    selected, _ = select_current_documents(documents)
    return {stage: sorted(doc.id for doc in docs) for stage, docs in selected.items()}


def _reuse_previous(run: models.OfficialRun, documents) -> dict | None:
    """Инкрементальное обновление при дозагрузке (ТЗ 9.2).

    Каждый параметр матрицы читает все три стадии, поэтому затронутые
    параметры определяются составом АКТУАЛЬНЫХ редакций: если дозагрузка его
    не изменила (дубликат, заменённая или неутверждённая редакция), прежний
    результат остаётся верным и пересчитывать нечего. Прежний прогон,
    выполненный не полностью, не переиспользуется: неполноту нельзя
    унаследовать как готовый ответ.
    """
    history = run.protocol_history or []
    previous = history[-1].get("result") if history else None
    if not previous:
        return None
    if (previous.get("coverage") or {}).get("not_run"):
        return None
    before = (previous.get("document_selection") or {}).get("selected") or {}
    if {k: sorted(v) for k, v in before.items()} != _selection(documents):
        return None
    reused = copy.deepcopy(previous)
    reused["incremental"] = {
        "reused_from_version": history[-1].get("version"),
        "reason": "дозагрузка не изменила состав актуальных редакций",
    }
    return reused


def _affected_codes(run: models.OfficialRun, documents) -> tuple[set[str] | None, dict | None]:
    """Параметры, которые надо пересчитать после дозагрузки (ТЗ 9.2).

    Возвращает (коды, прежний результат) или (None, None) — пересчитать всё.
    Пересчитываются параметры, для которых в новых документах есть данные, а
    также те, чья прежняя проверка неполна или ссылается на редакцию, которая
    перестала быть актуальной. Остальные результаты и решения инспектора по
    ним переносятся без изменений.
    """
    history = run.protocol_history or []
    previous = history[-1].get("result") if history else None
    if not previous or (previous.get("coverage") or {}).get("not_run"):
        return None, None
    if previous.get("matrix_version") != current_matrix_version():
        return None, None  # матрица изменилась — прежние результаты не по той редакции
    before = {int(i) for ids in ((previous.get("document_selection") or {})
                                 .get("selected") or {}).values() for i in ids}
    after = {i for ids in _selection(documents).values() for i in ids}
    removed = before - after
    added = [document for document in documents if document.id in after - before]
    parameters = list_parameters()
    previous_checks = {item.get("parameter_code"): item for item in previous.get("checks") or []}
    codes = parameters_with_new_data(parameters, added)
    for parameter in parameters:
        check = previous_checks.get(parameter["code"])
        if (check is None or check.get("technical_status") != "completed"
                or check.get("completeness_status") not in {"COMPLETE", "NOT_APPLICABLE"}
                or any(item.get("document_id") in removed
                       for item in check.get("evidence") or [])):
            codes.add(parameter["code"])
    if len(codes) >= len(parameters):
        return None, None
    return codes, previous


def _merge_incremental(result: dict, previous: dict, codes: set[str],
                       decision_version: int) -> dict:
    """Новые результаты по затронутым параметрам, прежние — по остальным."""
    fresh = {item["parameter_code"]: item for item in result.get("checks") or []}
    kept = {item.get("parameter_code"): item for item in previous.get("checks") or []}
    checks = []
    for parameter in list_parameters():
        code = parameter["code"]
        if code in codes and code in fresh:
            # Решения, принятые по прежнему результату, к новому не относятся.
            checks.append({**fresh[code], "decisions_after_version": decision_version})
        elif code in kept:
            checks.append(kept[code])
    completed = sum(item.get("technical_status") == "completed" for item in checks)
    result["checks"] = checks
    result["coverage"] = {"total": len(checks), "completed": completed,
                          "not_run": len(checks) - completed}
    result["incremental"] = {
        "recomputed": sorted(codes), "kept": len(checks) - len(codes & fresh.keys()),
        "reason": "пересчитаны параметры, для которых появились новые данные, и "
                  "параметры с неполной прежней проверкой; остальные перенесены "
                  "вместе с решениями инспектора",
    }
    return result


def _execute(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.get(models.OfficialRun, run_id)
        if run is None:
            return
        if run.cancelled_at is not None:
            run.status = "cancelled"
            run.stage = "Остановлено до начала проверки"
            db.commit()
            return
        run.status = "running"
        db.commit()
        stored = [db.get(models.Document, item["id"]) for item in run.input_snapshot]
        if any(document is None for document in stored):
            run.status, run.error = "error", "один из документов удалён после запуска"
            db.commit()
            return
        if any(document.digest != snapshot["digest"]
               for document, snapshot in zip(stored, run.input_snapshot, strict=True)):
            run.status, run.error = "error", "содержимое документа изменилось после запуска"
            db.commit()
            return
        # Проверка использует снимок метаданных на момент запуска. Последующее
        # исправление карточки документа не переписывает уже начатый протокол.
        documents = [SimpleNamespace(
            id=document.id, name=document.name, file_path=document.file_path,
            pages=document.pages, digest=snapshot["digest"],
            source_metadata=copy.deepcopy(snapshot["metadata"]),
        ) for document, snapshot in zip(stored, run.input_snapshot, strict=True)]

        # Сессия SQLAlchemy не потокобезопасна, а сверка идёт параллельно:
        # cancelled() вызывается из рабочих потоков. Раньше она трогала общую
        # сессию одновременно с записью прогресса, и прогон падал ошибкой
        # «строка удалена» посреди работы. Теперь проверка остановки читает
        # своей короткой сессией, а запись прогресса идёт под блокировкой.
        progress_lock = threading.Lock()

        def progress(stage: str, completed: int, total: int) -> None:
            with progress_lock:
                db.expire(run)
                run.stage, run.completed, run.total = stage, completed, total
                db.commit()

        def cancelled() -> bool:
            with SessionLocal() as probe:
                row = probe.get(models.OfficialRun, run_id)
                return row is None or row.cancelled_at is not None

        config = _settings_config(db)
        run.model_version = config.resolved_model()
        db.commit()
        reused = _reuse_previous(run, documents)
        if reused is not None:
            run.result = reused
            run.completed = run.total = reused["coverage"]["total"]
            run.status, run.stage = "completed", "Готово: актуальные редакции не изменились"
            db.commit()
            return
        codes, previous = _affected_codes(run, documents)
        result = None
        for attempt in range(EXECUTE_RETRIES + 1):
            try:
                result = run_official_analysis(
                    documents, config, include_medium=run.include_medium,
                    progress=progress, cancelled=cancelled, codes=codes,
                )
                break
            except Exception:  # noqa: BLE001 — после повторов сбой уйдёт в статус error
                if attempt == EXECUTE_RETRIES or cancelled():
                    raise
        db.expire(run)
        if codes is not None:
            result = _merge_incremental(result, previous, codes, _version(db, run_id))
        elif run.protocol_history:
            result["incremental"] = {
                "recomputed": "all",
                "reason": "прежний результат неполон или матрица изменилась — "
                          "пересчитаны все параметры",
            }
        # Свободный поиск гипотез вне матрицы (ТЗ 9.5) — по тем же редакциям.
        result["free_search"] = free_search.run(db, run, documents, result)
        run.result = result
        run.completed = result["coverage"]["total"]
        run.total = result["coverage"]["total"]
        run.status = "cancelled" if run.cancelled_at is not None else "completed"
        run.stage = "Остановлено" if run.status == "cancelled" else "Готово"
        db.commit()
    except Exception as exc:  # noqa: BLE001 — сбой виден в статусе, не становится чистым результатом
        run = db.get(models.OfficialRun, run_id)
        if run is not None:
            run.status, run.error = "error", f"проверка не выполнена: {exc}"
            db.commit()
    finally:
        db.close()


@router.post("/runs", dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def create_run(body: RunInput, background: BackgroundTasks, db: Session = Depends(get_session)):
    if not body.object_id.strip() or not body.document_ids:
        raise HTTPException(422, "выберите документы одного объекта")
    if len(set(body.document_ids)) != len(body.document_ids):
        raise HTTPException(422, "документ выбран повторно")
    rows = db.query(models.Document).filter(models.Document.id.in_(body.document_ids)).all()
    if len(rows) != len(body.document_ids):
        raise HTTPException(404, "один или несколько документов не найдены")
    for row in rows:
        metadata = row.source_metadata or {}
        if metadata.get("object_id") != body.object_id:
            raise HTTPException(422, "в комплекте смешаны разные объекты")
        if metadata.get("stage") not in STAGES:
            raise HTTPException(422, "для документа не задана стадия")
        if row.status != "ok":
            raise HTTPException(409, f"документ «{row.name}» ещё не разобран")
    ordered = sorted(rows, key=lambda row: body.document_ids.index(row.id))
    snapshot = [{"id": row.id, "digest": row.digest,
                 "metadata": copy.deepcopy(row.source_metadata)} for row in ordered]
    run = models.OfficialRun(
        object_id=body.object_id.strip(), status="queued", input_snapshot=snapshot,
        include_medium=body.include_medium,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(_execute, run.id)
    return _run_dict(db, run)


@router.get("/runs")
def runs(db: Session = Depends(get_session)):
    rows = db.query(models.OfficialRun).order_by(models.OfficialRun.created_at.desc()).all()
    return [_run_dict(db, row) for row in rows]


@router.get("/runs/{run_id}")
def run(run_id: int, db: Session = Depends(get_session)):
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    return _run_dict(db, row)


@router.post("/runs/{run_id}/cancel", dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def cancel_run(run_id: int, db: Session = Depends(get_session)):
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    if row.status in {"queued", "running"}:
        row.cancelled_at = dt.datetime.utcnow()
        row.stage = "Остановка на безопасной точке"
        db.commit()
    return _run_dict(db, row)


def _event(db: Session, run_id: int, action: str, author: str = "", reason: str = "") -> None:
    db.add(models.ProtocolEvent(run_id=run_id, action=action, author=author, reason=reason))


@router.post("/runs/{run_id}/finalize")
def finalize_run(run_id: int, body: FinalizeInput, db: Session = Depends(get_session),
                 user: auth.Principal = Depends(auth.require(*auth.VERIFIERS))):
    """Финализация протокола («Завершить», ТЗ 9.3, п.4).

    Разрешена, только когда у каждого кандидата есть решение инспектора или
    он явно переведён в CLARIFICATION_REQUIRED. После неё решения и
    дозагрузка закрыты.
    """
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    if row.finalized_at is not None:
        raise HTTPException(409, "протокол уже финализирован")
    if row.status != "completed" or not row.result:
        raise HTTPException(409, "финализировать можно только сформированный протокол")
    pending = protocol.pending_candidates(_run_dict(db, row)["result"])
    if pending:
        raise HTTPException(409, "есть кандидаты без решения инспектора: " + ", ".join(pending))
    row.finalized_at = dt.datetime.utcnow()
    row.finalized_by = user.display
    _event(db, run_id, "FINALIZE", row.finalized_by)
    db.commit()
    db.refresh(row)
    return _run_dict(db, row)


@router.post("/runs/{run_id}/unfinalize")
def unfinalize_run(run_id: int, body: UnfinalizeInput, db: Session = Depends(get_session),
                   user: auth.Principal = Depends(auth.require("supervisor"))):
    """Отмена финализации — только администратор или супервизор, с причиной."""
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    if not body.reason.strip():
        raise HTTPException(422, "укажите причину отмены")
    if row.finalized_at is None:
        raise HTTPException(409, "протокол не финализирован")
    row.finalized_at = None
    row.finalized_by = ""
    _event(db, run_id, "UNFINALIZE", user.display, body.reason.strip())
    db.commit()
    db.refresh(row)
    return _run_dict(db, row)


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, db: Session = Depends(get_session)):
    rows = (db.query(models.ProtocolEvent).filter_by(run_id=run_id)
            .order_by(models.ProtocolEvent.id).all())
    return [{"action": row.action, "author": row.author, "reason": row.reason,
             "created_at": row.created_at.isoformat()} for row in rows]


@router.post("/runs/{run_id}/decisions")
def decide(run_id: int, body: DecisionInput, db: Session = Depends(get_session),
           user: auth.Principal = Depends(auth.require(*auth.VERIFIERS))):
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    if row.status != "completed" or not row.result:
        raise HTTPException(409, "решение можно сохранить только для завершённой проверки")
    if row.finalized_at is not None:
        raise HTTPException(409, "протокол финализирован: решения изменить нельзя")
    if body.status not in DECISION_STATUSES:
        raise HTTPException(422, "неизвестный статус решения")
    reason_code = body.reason_code.strip().upper()
    if body.status == "NEGATIVE_VERIFIED" and reason_code not in protocol.REASON_CODES:
        raise HTTPException(422, "при отклонении нужна кодированная причина: "
                                 + ", ".join(protocol.REASON_CODES))
    if not body.reason.strip():
        raise HTTPException(422, "укажите основание решения")
    current = _version(db, run_id)
    if body.expected_version != current:
        raise HTTPException(409, "протокол уже изменён; обновите страницу")
    check = next((item for item in _result_checks(row.result)
                  if item.get("finding_id") == body.finding_id), None)
    if check is None:
        raise HTTPException(404, "проверка параметра не найдена")
    evidence = check.get("evidence") or []
    if body.status in {"CONFIRMED_VIOLATION", "NEGATIVE_VERIFIED"} and (
        check.get("completeness_status") != "COMPLETE"
        or not evidence
        or not all(item.get("bbox") for item in evidence)
    ):
        raise HTTPException(422, "экспертное решение требует полного комплекта доказательств")
    db.add(models.InspectorDecision(
        run_id=run_id, version=current + 1, finding_id=body.finding_id,
        status=body.status, author=user.display, reason=body.reason.strip(),
        reason_code=reason_code, user_id=user.user_id,
    ))
    # Разметка для GOLD-набора, лог отклонений и спорных случаев (ТЗ 9.4).
    feedback.record_decision(db, row, check, status=body.status, version=current + 1,
                             reason=body.reason.strip(), reason_code=reason_code,
                             source_versions=_source_versions(db, row), user_id=user.user_id)
    db.commit()
    db.refresh(row)
    return _run_dict(db, row)


def _source_versions(db: Session, run: models.OfficialRun) -> dict:
    """Версии источников метки (ТЗ 14.1): матрица, модель, входной манифест."""
    built = _run_dict(db, run)["protocol"]["versions"]
    return {key: built.get(key) for key in (
        "matrix_version", "model_version", "input_manifest_hash")}


SUSPICION_ACTIONS = {
    # действие: (допустимый текущий статус находки, новый статус, статус инспектора)
    "promote": ("SUSPICION", "CANDIDATE", "PROMOTED"),
    "dismiss": ("SUSPICION", "SUSPICION", "DISMISSED"),
    "confirm": ("CANDIDATE", "CONFIRMED_VIOLATION", "CONFIRMED"),
    "reject": ("CANDIDATE", "NEGATIVE_VERIFIED", "REJECTED"),
}


@router.get("/runs/{run_id}/suspicions")
def run_suspicions(run_id: int, db: Session = Depends(get_session)):
    """Гипотезы свободного поиска процесса в структуре ТЗ 9.5."""
    rows = (db.query(models.Suspicion).filter_by(run_id=run_id)
            .order_by(models.Suspicion.id).all())
    return [free_search.suspicion_dict(row) for row in rows]


@router.post("/runs/{run_id}/suspicions/{suspicion_id}")
def review_suspicion(run_id: int, suspicion_id: int, body: SuspicionReview,
                     db: Session = Depends(get_session),
                     user: auth.Principal = Depends(auth.require(*auth.VERIFIERS))):
    run_row = db.get(models.OfficialRun, run_id)
    item = db.get(models.Suspicion, suspicion_id)
    if run_row is None or item is None or item.run_id != run_id:
        raise HTTPException(404, "гипотеза не найдена")
    if run_row.finalized_at is not None:
        raise HTTPException(409, "протокол финализирован: решения изменить нельзя")
    if body.action not in SUSPICION_ACTIONS:
        raise HTTPException(422, "действие: " + ", ".join(SUSPICION_ACTIONS))
    required, finding_status, inspector_status = SUSPICION_ACTIONS[body.action]
    if item.finding_status != required or item.inspector_status == "DISMISSED":
        raise HTTPException(409, f"действие недоступно для статуса {item.finding_status}")
    reason_code = body.reason_code.strip().upper()
    if body.action == "reject" and reason_code not in protocol.REASON_CODES:
        raise HTTPException(422, "при отклонении нужна кодированная причина: "
                                 + ", ".join(protocol.REASON_CODES))
    if body.action in {"dismiss", "confirm", "reject"} and not body.comment.strip():
        raise HTTPException(422, "укажите основание решения")
    evidence = body.evidence if body.evidence is not None else list(item.evidence or [])
    if body.action == "promote" and not free_search.has_coordinates(evidence):
        raise HTTPException(422, "для перевода в кандидаты нужны источники с листом и "
                                 "координатами в ПД и в РД или ИД")
    item.evidence = evidence
    item.finding_status, item.inspector_status = finding_status, inspector_status
    item.inspector_comment = " ".join(part for part in (reason_code, body.comment.strip())
                                      if part)
    item.reviewed_by, item.reviewed_at = user.user_id, dt.datetime.utcnow()
    if finding_status in feedback.LABELS:
        feedback.record_decision(
            db, run_row, {"finding_id": f"S-{item.id}", "parameter_code": item.parameter_code,
                          "finding_status": "CANDIDATE", "explanation": item.description,
                          "evidence": evidence},
            status=finding_status, version=_version(db, run_id), reason=body.comment.strip(),
            reason_code=reason_code, source_versions=_source_versions(db, run_row),
            user_id=user.user_id)
    # Протокол читает гипотезы из результата процесса — обновляем и его.
    result = copy.deepcopy(run_row.result or {})
    free = result.setdefault("free_search", {"status": "completed", "items": []})
    free["items"] = [free_search.suspicion_dict(item) if entry.get("suspicion_id") == item.id
                     else entry for entry in free.get("items") or []]
    run_row.result = result
    db.add(models.ProtocolEvent(run_id=run_id, action=f"SUSPICION_{body.action.upper()}",
                                author=user.display, reason=item.inspector_comment))
    db.commit()
    return free_search.suspicion_dict(item)


@router.get("/runs/{run_id}/export")
def export_run(
    run_id: int,
    output_format: str = Query("json", alias="format"),
    db: Session = Depends(get_session),
):
    row = db.get(models.OfficialRun, run_id)
    if row is None:
        raise HTTPException(404, "прогон не найден")
    payload = _run_dict(db, row)
    if output_format == "json":
        return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                        media_type="application/json",
                        headers={
                            "Content-Disposition": f'attachment; filename="run-{run_id}.json"'
                        })
    if output_format != "csv":
        raise HTTPException(422, "формат должен быть json или csv")
    stream = io.StringIO()
    fields = ["finding_id", "parameter_code", "parameter_name", "priority",
              "completeness_status", "finding_status", "technical_status",
              "expected_value", "actual_value", "explanation"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for check in _result_checks(payload.get("result")):
        writer.writerow({field: check.get(field) for field in fields})
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="run-{run_id}.csv"'})


# Контракт асинхронной pull-модели: POST запускает процесс и сразу отдаёт
# process_id со статусом queued; GET по process_id возвращает статус
# (queued → running → completed | cancelled | error), этап, прогресс и,
# по завершении, результат. Это те же обработчики, что /runs, под именами
# контракта — второй реализации нет, расходиться нечему.
@router.post("/processes", summary="Запустить процесс проверки",
             dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def start_process(body: RunInput, background: BackgroundTasks,
                  db: Session = Depends(get_session)):
    return create_run(body, background, db)


@router.get("/processes/{process_id}", summary="Статус и результат процесса")
def process_status(process_id: int, db: Session = Depends(get_session)):
    return run(process_id, db)


@router.post("/processes/{process_id}/cancel", summary="Остановить процесс",
             dependencies=[Depends(auth.require(*auth.VERIFIERS))])
def cancel_process(process_id: int, db: Session = Depends(get_session)):
    return cancel_run(process_id, db)
