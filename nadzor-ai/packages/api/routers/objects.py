"""Объекты капитального строительства и их документы."""
from __future__ import annotations

import asyncio
from pathlib import Path

from analysis.pipeline import load_document
from documents.intake import detect_type, new_file_id, read_with_limit, sha256_of, storage_key
from documents.pdf import render_png
from documents.schemas import IntakeError, StateKind
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from integrations.identity.ports import Principal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit_store import record
from api.deps import permission, session_dep
from api.models import ConstructionObject, DocumentRow
from api.rbac import scope_objects
from api.state import state

router = APIRouter(prefix="/api", tags=["Объекты"])


async def _object_or_denied(session: AsyncSession, object_id: str,
                            principal: Principal) -> ConstructionObject:
    """Выборка объекта с проверкой прав на уровне данных.

    Права проверяются в самом запросе: без совпадения по области видимости
    строка не возвращается вовсе.
    """
    stmt = scope_objects(select(ConstructionObject).where(ConstructionObject.id == object_id),
                         principal)
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Объект не найден или недоступен.")
    return obj


@router.get("/objects", summary="Журнал объектов")
async def list_objects(session: AsyncSession = Depends(session_dep),
                       principal: Principal = Depends(permission("objects:read"))) -> dict:
    stmt = scope_objects(select(ConstructionObject).order_by(ConstructionObject.name), principal)
    rows = (await session.execute(stmt)).scalars().all()
    return {"items": [_brief(o) for o in rows], "total": len(rows)}


@router.get("/objects/lookup", summary="Автозаполнение по номеру разрешения")
async def lookup(permit: str,
                 principal: Principal = Depends(permission("objects:read"))) -> dict:
    """Карточка объекта из ИАИС ОГД. Источник сведений возвращается явно."""
    card = await state.urban_data.object_by_permit(permit)
    if card is None:
        return {"found": False, "source": state.urban_data.name,
                "message": "Сведения по указанному разрешению не найдены. Объект можно создать "
                           "вручную — он будет помечен как не подтверждённый ИАИС ОГД."}
    participants = await state.urban_data.participants(permit)
    return {"found": True, "source": card.source, "card": card.__dict__,
            "participants": participants}


@router.get("/objects/{object_id}", summary="Карточка объекта")
async def get_object(object_id: str, session: AsyncSession = Depends(session_dep),
                     principal: Principal = Depends(permission("objects:read"))) -> dict:
    obj = await _object_or_denied(session, object_id, principal)
    docs = (await session.execute(
        select(DocumentRow).where(DocumentRow.object_id == object_id)
        .order_by(DocumentRow.state_kind, DocumentRow.title))).scalars().all()
    history = await state.urban_data.inspection_history(obj.permit_number)
    return {"object": _full(obj), "documents": [_doc(d) for d in docs],
            "inspections": [h.__dict__ for h in history]}


@router.post("/objects/{object_id}/documents", summary="Загрузить документ комплекта")
async def upload_document(object_id: str, file: UploadFile = File(...), state_hint: str = Form(""),
                          session: AsyncSession = Depends(session_dep),
                          principal: Principal = Depends(permission("objects:create"))) -> dict:
    """Приём файла: тип по сигнатуре, лимиты, сохранение, разбор и извлечение фактов.

    Имя файла, присланное пользователем, не участвует в формировании путей.
    Инспектор сам указывает состояние (ПД/РД/ИД и т.д.), если оно не определяется
    по заголовку листа.
    """
    limits = state.limits_config["rate_limit"]
    state.rate_limiter.check(principal.subject, "upload",
                             limits["uploads_per_minute"], window_seconds=60)
    obj = await _object_or_denied(session, object_id, principal)
    data = await read_with_limit(file, state.limits_config["upload"])
    kind, _mime = detect_type(data[:512], data)
    allowed = set(state.limits_config["upload"]["allowed_signatures"])
    if kind not in allowed:
        raise IntakeError(f"Файлы типа «{kind}» не принимаются в этом контуре.")

    hint = None
    if state_hint:
        try:
            hint = StateKind(state_hint)
        except ValueError as exc:
            raise IntakeError("Неизвестный тип состояния документа.") from exc

    doc_id = f"{obj.id}-{sha256_of(data)[:8]}"
    existing = await session.get(DocumentRow, doc_id)
    if existing is not None:
        # Тот же файл (по содержимому) уже загружен в этот объект — повторно не сохраняем.
        return _doc(existing)

    file_id = new_file_id()
    path = state.storage_root / storage_key(obj.id, file_id, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    # Разбор PDF (PyMuPDF) — синхронный и потенциально тяжёлый на плотных или
    # намеренно патологических файлах. В отдельном потоке — чтобы не блокировать
    # цикл событий для всех остальных запросов API; с таймаутом — чтобы падение
    # не роняло приложение (Приложение Б.4).
    timeout = state.limits_config["processing"]["worker_timeout_seconds"]
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(load_document, path, obj.id, title=file.filename or "",
                              hint_state=hint),
            timeout=timeout)
    except TimeoutError as exc:
        raise IntakeError(
            f"Разбор документа не уложился в {timeout} с. Файл слишком велик или сложен "
            "для обработки — разделите его на части.") from exc
    row = DocumentRow(
        id=parsed.id, object_id=obj.id, state_kind=parsed.state_kind.value,
        doc_kind=parsed.doc_kind.value, title=parsed.title, path=str(path),
        revision=parsed.revision, doc_date=parsed.doc_date, page_count=parsed.page_count,
        sha256=sha256_of(data), signature_status="not_checked", facts_count=len(parsed.facts))
    session.add(row)
    record(session, principal.subject, principal.roles[0] if principal.roles else "",
          "document.upload", "document", parsed.id,
          {"object_id": obj.id, "state_kind": row.state_kind, "doc_kind": row.doc_kind,
           "size_bytes": len(data)})
    await session.commit()
    return _doc(row)


@router.get("/documents/{document_id}", summary="Карточка документа")
async def get_document(document_id: str, session: AsyncSession = Depends(session_dep),
                       principal: Principal = Depends(permission("objects:read"))) -> dict:
    doc = await _document_or_denied(session, document_id, principal)
    return _doc(doc)


@router.get("/documents/{document_id}/page/{page}", summary="Лист документа изображением")
async def document_page(document_id: str, page: int,
                        session: AsyncSession = Depends(session_dep),
                        principal: Principal = Depends(permission("objects:read"))) -> Response:
    """Отрисовка листа для перехода от гипотезы к области на документе."""
    doc = await _document_or_denied(session, document_id, principal)
    path = Path(doc.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл документа недоступен в хранилище.")
    png = render_png(path.read_bytes(), page)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300"})


async def _document_or_denied(session: AsyncSession, document_id: str,
                              principal: Principal) -> DocumentRow:
    stmt = (select(DocumentRow).join(ConstructionObject,
                                     ConstructionObject.id == DocumentRow.object_id)
            .where(DocumentRow.id == document_id))
    stmt = scope_objects(stmt, principal)
    doc = (await session.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден или недоступен.")
    return doc


def _brief(o: ConstructionObject) -> dict:
    return {"id": o.id, "name": o.name, "address": o.address, "district": o.district,
            "permit_number": o.permit_number, "stage": o.stage, "developer": o.developer,
            "contractor": o.contractor, "planned_completion": o.planned_completion,
            "data_source": o.data_source, "assigned_to": o.assigned_to,
            "department": o.department, "protected": o.protected}


def _full(o: ConstructionObject) -> dict:
    return {**_brief(o), "cadastral_number": o.cadastral_number, "designer": o.designer,
            "permit_date": o.permit_date, "card": o.card}


def _doc(d: DocumentRow) -> dict:
    return {"id": d.id, "object_id": d.object_id, "title": d.title, "doc_kind": d.doc_kind,
            "state_kind": d.state_kind, "revision": d.revision,
            "doc_date": d.doc_date.isoformat() if d.doc_date else None,
            "page_count": d.page_count, "facts_count": d.facts_count,
            "signature_status": d.signature_status, "sha256": d.sha256}
