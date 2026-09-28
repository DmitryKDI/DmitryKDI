"""Внешний контракт по ТЗ: /api/v1, pull-модель с process_id.

POST /api/v1/documents/upload принимает файлы и реестр, сразу возвращает
process_id; проверка идёт в фоне, статус и протокол забираются по
process_id. Внутри — те же обработчики, что у интерфейса (`official_api`):
две реализации одного контракта разошлись бы.
"""
from __future__ import annotations

import csv
import io
import json

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import (
    auth,
    document_convert,
    external_sync,
    models,
    official_api,
    protocol,
    protocol_export,
    registry_xlsx,
)
from .db import get_session
from .official_pipeline import clarification_result

router = APIRouter(prefix="/api/v1", tags=["api-v1"])

# Лимиты загрузки из ТЗ (9.1, «Обработка ошибок при загрузке»).
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
# Перечень ИД, ред. 1.1 — обязательные поля реестра. Связь редакций задаётся
# predecessor_id и/или successor_id (идентификаторы file_id этого же реестра).
REGISTRY_FIELDS = ("file_id", "file_name", "object_id", "doc_stage", "discipline",
                   "document_code", "revision", "approval_status")


def _parse_registry(data: bytes, name: str) -> list[dict]:
    """Реестр файлов (перечень ИД): JSON-массив, CSV или XLSX с заголовком."""
    if name.lower().endswith(".xlsx") or data.startswith(b"PK\x03\x04"):
        try:
            rows = registry_xlsx.read_rows(data)
        except registry_xlsx.RegistryFormatError as exc:
            raise HTTPException(422, f"реестр XLSX не прочитан: {exc}") from exc
        return _check_registry(rows)
    text = data.decode("utf-8-sig")
    if name.lower().endswith(".json") or text.lstrip().startswith(("[", "{")):
        rows = json.loads(text)
        rows = rows.get("files", []) if isinstance(rows, dict) else rows
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    return _check_registry(rows)


def _check_registry(rows) -> list[dict]:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise HTTPException(422, "реестр должен быть списком записей о файлах")
    missing = [field for field in REGISTRY_FIELDS if rows and field not in rows[0]]
    if missing:
        raise HTTPException(422, "в реестре нет обязательных полей: " + ", ".join(missing))
    ids = [str(row.get("file_id") or "").strip() for row in rows]
    if any(not file_id for file_id in ids):
        raise HTTPException(422, "в реестре есть запись без file_id")
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "file_id в реестре повторяется")
    return rows


def _metadata(row: dict, predecessor: int | None) -> dict:
    try:
        return official_api.MetadataInput(
            object_id=str(row["object_id"]), stage=str(row["doc_stage"]).upper(),
            document_code=str(row["document_code"]), revision=str(row["revision"]),
            approval_status=str(row["approval_status"]).upper(),
            approval_date=row.get("approval_date") or None, predecessor_id=predecessor,
            signature_status=row.get("signature_status") or None,
            sheet_page_range=row.get("sheet_page_range") or None,
            discipline=str(row.get("discipline") or "").strip() or None,
            file_id=str(row.get("file_id") or "").strip() or None,
        ).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError("; ".join(err["msg"] for err in exc.errors())) from exc


def _document_by_file_id(db: Session, object_id: str, file_id: str) -> models.Document | None:
    """Документ объекта с данным file_id реестра (последний загруженный)."""
    if not file_id:
        return None
    for document in (db.query(models.Document).order_by(models.Document.id.desc())):
        metadata = document.source_metadata or {}
        if metadata.get("file_id") == file_id and (
                not object_id or metadata.get("object_id") == object_id):
            return document
    return None


def _payload(db: Session, run: models.OfficialRun) -> dict:
    body = official_api._run_dict(db, run)
    # Во внешнем контракте status — словарь ТЗ; внутреннее состояние фоновой
    # задачи остаётся рядом, чтобы сбой было видно причиной, а не догадкой.
    body["run_state"] = body["status"]
    body["status"] = body["process_status"]
    return body


def _run(db: Session, process_id: int) -> models.OfficialRun:
    run = db.get(models.OfficialRun, process_id)
    if run is None:
        raise HTTPException(404, "процесс не найден")
    return run


@router.post("/documents/upload", summary="Загрузить документы и получить process_id",
             dependencies=[Depends(auth.require(*auth.VERIFIERS, "service"))])
def upload(background: BackgroundTasks,
           files: list[UploadFile] = File(...),
           registry: UploadFile | None = File(None),
           process_id: int | None = Form(None),
           include_medium: bool = Form(False),
           db: Session = Depends(get_session)):
    from . import main  # приём файла живёт в main; импорт здесь разрывает цикл

    payloads = [(file.filename or "document", file.file.read()) for file in files]
    total = sum(len(data) for _, data in payloads)
    if total > MAX_PACKAGE_BYTES:
        raise HTTPException(413, f"пакет {total / 1048576:.1f} МБ больше допустимых "
                                 f"{MAX_PACKAGE_BYTES // 1048576} МБ")
    run = _run(db, process_id) if process_id is not None else None
    finalized = False
    if run is not None:
        status = _payload(db, run)["status"]
        # В финализированный протокол документы принимаются, но проверку не
        # запускают: инспектор получает уведомление (ТЗ 9.6).
        finalized = status == protocol.FINALIZED
        if not finalized and not protocol.can_upload(status):
            raise HTTPException(409, f"дозагрузка невозможна в статусе {status}")

    rows = _parse_registry(registry.file.read(), registry.filename or "") if registry else []
    by_name = {str(row["file_name"]): row for row in rows}
    object_ids = {str(row["object_id"]) for row in rows}
    if run is not None:
        object_ids.add(run.object_id)
    if len(object_ids) > 1:
        raise HTTPException(422, "в пакете смешаны разные объекты: "
                                 + ", ".join(sorted(object_ids)))

    accepted, rejected, stored = [], [], {}
    for name, data in payloads:
        if len(data) > MAX_FILE_BYTES:
            rejected.append({"file_name": name, "reason": f"файл больше "
                             f"{MAX_FILE_BYTES // 1048576} МБ"})
            continue
        try:
            main.scan_or_reject(data)
        except HTTPException as exc:
            if exc.status_code == 503:
                raise  # антивирус недоступен — не принимать пакет вовсе
            rejected.append({"file_name": name, "reason": str(exc.detail)})
            continue
        try:
            converted = document_convert.to_pdf(data, name)
        except document_convert.UnsupportedFormatError as exc:
            rejected.append({"file_name": name, "reason": str(exc)})
            continue
        row = by_name.get(name)
        if rows and row is None:
            rejected.append({"file_name": name, "reason": "файла нет в реестре"})
            continue
        expected_sha = str((row or {}).get("sha256") or "").strip().lower()
        # Контрольная сумма из реестра — от исходного файла, а не от PDF,
        # в который DOCX/XML преобразуется при приёме.
        if expected_sha and expected_sha != converted.source_sha256:
            rejected.append({"file_name": name, "reason": "SHA-256 не совпадает с реестром"})
            continue
        # Перечень ИД: «Перезапись файла под тем же file_id запрещена».
        # Тот же файл повторно — допустим; другое содержимое под старым
        # file_id — отказ: исправленный файл приходит новой записью.
        previous = _document_by_file_id(db, str((row or {}).get("object_id") or ""),
                                        str((row or {}).get("file_id") or "").strip())
        if previous is not None and (previous.source_metadata or {}).get(
                "source_sha256") != converted.source_sha256:
            rejected.append({"file_name": name, "reason": "file_id уже занят файлом с другим "
                             "содержимым; перезапись запрещена — загрузите файл с новым "
                             "file_id и укажите predecessor_id"})
            continue
        try:
            doc = main.ingest_pdf(db, converted.pdf, name, "before", background)
        except HTTPException as exc:
            rejected.append({"file_name": name, "reason": str(exc.detail)})
            continue
        stored[name] = (doc, row, converted)

    # Связь редакций в реестре задаётся идентификаторами реестра; переводим
    # их в идентификаторы документов после того, как все файлы приняты.
    registry_ids = {str(row.get("file_id")): doc.id
                    for name, (doc, row, _) in stored.items() if row and row.get("file_id")}
    # Связь можно задать с любой стороны: predecessor_id у новой редакции или
    # successor_id у заменённой (перечень ИД). Ссылка может вести и на файл
    # из прежней загрузки того же объекта.
    successors = {str(row.get("successor_id")).strip(): str(row.get("file_id")).strip()
                  for _, (_, row, _) in stored.items() if row and row.get("successor_id")}

    def document_id(file_id: str) -> int | None:
        if not file_id:
            return None
        if file_id in registry_ids:
            return registry_ids[file_id]
        found = _document_by_file_id(db, next(iter(object_ids), ""), file_id)
        return found.id if found is not None else None

    snapshot_add = []
    for name, (doc, row, converted) in stored.items():
        if row is not None:
            own = str(row.get("file_id") or "").strip()
            predecessor = (document_id(str(row.get("predecessor_id") or "").strip())
                           or document_id(successors.get(own, "")))
            try:
                doc.source_metadata = {
                    **_metadata(row, predecessor),
                    # Исходный формат и его отпечаток: доказательство ссылается
                    # на страницу преобразованного PDF, и это должно быть видно.
                    "source_format": converted.source_format,
                    "source_sha256": converted.source_sha256,
                }
            except ValueError as exc:
                rejected.append({"file_name": name, "reason": f"реестр: {exc}"})
                continue
            doc.side = "before" if doc.source_metadata["stage"] == "PD" else "after"
        snapshot_add.append({"id": doc.id, "digest": doc.digest,
                             "metadata": dict(doc.source_metadata or {})})
        accepted.append({"file_name": name, "document_id": doc.id,
                         "stage": (doc.source_metadata or {}).get("stage")})
    if not snapshot_add:
        db.rollback()
        raise HTTPException(422, {"message": "ни один файл не принят", "rejected": rejected})

    if finalized:
        run.pending_documents = [*(run.pending_documents or []), *snapshot_add]
        db.add(models.ProtocolEvent(
            run_id=run.id, action="NEW_DOCUMENTS_AFTER_FINALIZATION",
            reason=", ".join(item["file_name"] for item in accepted)))
        db.commit()
        body = _payload(db, run)
        return {"process_id": run.id, "status": body["status"],
                "upload_status": body["protocol"]["upload_status"],
                "scenario": body["protocol"]["scenario"],
                "accepted": accepted, "rejected": rejected,
                "notice": "протокол финализирован: новые документы сохранены, проверка не "
                          "запускалась; для их проверки создайте новый процесс"}

    object_id = next(iter(object_ids), "")
    if run is None:
        run = models.OfficialRun(object_id=object_id, status="queued", input_snapshot=[],
                                 include_medium=include_medium)
        db.add(run)
        db.flush()
    else:
        # Дозагрузка: прежняя версия протокола остаётся в истории (ТЗ 9.2).
        run.protocol_history = [*(run.protocol_history or []), {
            "version": len(run.protocol_history or []) + 1, "result": run.result,
            "input_snapshot": run.input_snapshot}]
        run.result, run.status, run.cancelled_at = None, "queued", None
        db.add(models.ProtocolEvent(run_id=run.id, action="RELOAD",
                                    reason=", ".join(item["file_name"] for item in accepted)))
    run.input_snapshot = [*(run.input_snapshot or []), *snapshot_add]

    if not rows and not any(item["metadata"].get("stage") for item in run.input_snapshot):
        # Без реестра сопоставимые редакции выбрать нельзя: пакет принят,
        # но каждый параметр честно ждёт уточнения (перечень ИД).
        run.result = clarification_result(
            object_id, "пакет загружен без реестра файлов: стадия, шифр и редакция "
                       "не определены", include_medium=run.include_medium)
        run.status, run.stage = "completed", "Требуется реестр файлов"
        run.completed = run.total = len(run.result["checks"])
    else:
        background.add_task(official_api._execute, run.id)
    db.commit()
    db.refresh(run)
    body = _payload(db, run)
    return {"process_id": run.id, "status": body["status"],
            "upload_status": body["protocol"]["upload_status"],
            "scenario": body["protocol"]["scenario"],
            "accepted": accepted, "rejected": rejected}


@router.get("/processes/{process_id}/status", summary="Мониторинг статуса процесса")
def status(process_id: int, db: Session = Depends(get_session)):
    body = _payload(db, _run(db, process_id))
    return {key: body[key] for key in (
        "process_id", "status", "run_state", "verification_status", "stage", "completed",
        "total", "error", "protocol_version", "sync_status")} | {
        "scenario": body["protocol"]["scenario"],
        "upload_status": body["protocol"]["upload_status"]}


@router.get("/processes/{process_id}", summary="Протокол и результат процесса")
def process(process_id: int, db: Session = Depends(get_session)):
    return _payload(db, _run(db, process_id))


@router.post("/processes/{process_id}/decisions", summary="Решение инспектора по кандидату")
def decide(process_id: int, body: official_api.DecisionInput,
           db: Session = Depends(get_session),
           user: auth.Principal = Depends(auth.require(*auth.VERIFIERS))):
    status_now = _payload(db, _run(db, process_id))["status"]
    if not protocol.can_verify(status_now):
        raise HTTPException(409, f"верификация невозможна в статусе {status_now}")
    official_api.decide(process_id, body, db, user)
    return _payload(db, _run(db, process_id))


@router.post("/processes/{process_id}/finalize", summary="Финализировать протокол")
def finalize(process_id: int, body: official_api.FinalizeInput,
             db: Session = Depends(get_session),
             user: auth.Principal = Depends(auth.require(*auth.VERIFIERS))):
    official_api.finalize_run(process_id, body, db, user)
    return _payload(db, _run(db, process_id))


@router.post("/processes/{process_id}/unfinalize", summary="Отменить финализацию")
def unfinalize(process_id: int, body: official_api.UnfinalizeInput,
               db: Session = Depends(get_session),
               user: auth.Principal = Depends(auth.require("supervisor"))):
    official_api.unfinalize_run(process_id, body, db, user)
    return _payload(db, _run(db, process_id))


@router.get("/processes/{process_id}/export", summary="Протокол в JSON, XML, DOCX или PDF")
def export(process_id: int, output_format: str = Query("json", alias="format"),
           db: Session = Depends(get_session)):
    payload = _payload(db, _run(db, process_id))
    name = f"protocol-{process_id}"
    formats = {
        "json": (lambda p: json.dumps(p, ensure_ascii=False, indent=2).encode(),
                 "application/json"),
        "xml": (protocol_export.to_xml, "application/xml"),
        "docx": (protocol_export.to_docx, "application/vnd.openxmlformats-officedocument."
                                          "wordprocessingml.document"),
        "pdf": (protocol_export.to_pdf, "application/pdf"),
    }
    if output_format not in formats:
        raise HTTPException(422, "формат: " + ", ".join(formats))
    build, media = formats[output_format]
    return Response(build(payload), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{name}.{output_format}"'})


@router.post("/inspection/{process_id}", summary="Передача результатов во внешнюю систему",
             dependencies=[Depends(auth.require(*auth.VERIFIERS, "service"))])
def inspection(process_id: int, db: Session = Depends(get_session)):
    """Передаются только подтверждённые инспектором записи финализированного
    протокола вместе с версиями и реестром входных файлов (ТЗ 9.3, п.4).

    Передача включается адресом приёма (`external_sync.URL_ENV`). Без него
    пакет формируется и возвращается, а статус честно LOCAL_ONLY: выдать его
    за отправленный значило бы скрыть, что передачи не было. Недоступность
    приёмника даёт PENDING_SYNC и повторы через 1, 5 и 15 минут; окончательный
    отказ — SEND_FAILED с причиной (ТЗ 9.6).
    """
    run = _run(db, process_id)
    payload = _payload(db, run)
    if payload["status"] != protocol.FINALIZED:
        raise HTTPException(409, "передача возможна только для финализированного протокола")
    proto = payload["protocol"]
    package = {"process_id": process_id,
               "versions": proto["versions"],
               "input_files": [{"sha256": item["digest"], **item["metadata"],
                                "file_id": item["metadata"].get("file_id") or f"D{item['id']}"}
                               for item in run.input_snapshot or []],
               "confirmed_violations": proto["tables"]["confirmed_violations"]}
    key = f"nadzor-{process_id}-v{len(run.protocol_history or []) + 1}"
    if run.sync_key != key:
        # Новая версия протокола — новый отсчёт попыток.
        run.sync_attempts, run.sync_next_at = 0, None
    run.sync_package, run.sync_key = package, key
    result = external_sync.attempt(db, run)
    return {**package, "sync_status": result.status, "sync_detail": result.detail,
            "sync_attempts": run.sync_attempts,
            "sync_next_at": run.sync_next_at.isoformat() if run.sync_next_at else None}
