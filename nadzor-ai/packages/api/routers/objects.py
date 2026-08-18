"""Объекты капитального строительства и их документы."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import permission, session_dep
from api.models import ConstructionObject, DocumentRow
from api.rbac import scope_objects
from api.state import state
from documents.pdf import render_png
from integrations.identity.ports import Principal

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
