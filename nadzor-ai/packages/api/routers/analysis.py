"""Запуски анализа, гипотезы, карта внимания и проекты документов."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.attention import build_attention_map
from analysis.diff.raster import RasterDiffEngine
from analysis.lifecycle import available_transitions
from analysis.models import Finding
from analysis.pipeline import build_states
from analysis.reporting import build_inspection_act, build_prescription
from analysis.runner import STAGES, object_card_for_rules, parse_documents, run_analysis
from api.audit_store import record
from api.deps import permission, session_dep
from api.models import AnalysisRun, Assessment, ConstructionObject, DocumentRow, FindingRow
from api.routers.objects import _object_or_denied
from api.state import state
from integrations.identity.ports import Principal

router = APIRouter(prefix="/api", tags=["Анализ"])


@router.get("/runs", summary="Журнал анализа документации")
async def list_runs(session: AsyncSession = Depends(session_dep),
                    principal: Principal = Depends(permission("objects:read"))) -> dict:
    from api.rbac import scope_objects
    stmt = scope_objects(
        select(AnalysisRun, ConstructionObject)
        .join(ConstructionObject, ConstructionObject.id == AnalysisRun.object_id)
        .order_by(AnalysisRun.started_at.desc()), principal)
    rows = (await session.execute(stmt)).all()
    return {"items": [{
        "id": run.id, "object_id": run.object_id, "object_name": obj.name,
        "transitions": run.transitions, "status": run.status, "stage": run.stage,
        "started_at": run.started_at.isoformat(), "documents": run.documents_count,
        "findings": run.findings_count, "provenance": run.provenance,
    } for run, obj in rows], "stages": STAGES}


@router.get("/objects/{object_id}/transitions", summary="Доступные переходы")
async def transitions(object_id: str, session: AsyncSession = Depends(session_dep),
                      principal: Principal = Depends(permission("objects:read"))) -> dict:
    await _object_or_denied(session, object_id, principal)
    docs = await _parsed_docs(session, object_id, principal)
    states = build_states(docs, object_id)
    return {"states": [{"kind": s.state_kind.value, "revision": s.revision, "label": s.label,
                        "documents": len(s.docs)} for s in states],
            "transitions": available_transitions(states)}


@router.post("/analysis/run", summary="Запустить анализ")
async def run(payload: dict, session: AsyncSession = Depends(session_dep),
              principal: Principal = Depends(permission("analysis:run"))) -> dict:
    object_id = payload.get("object_id", "")
    obj = await _object_or_denied(session, object_id, principal)
    chosen = payload.get("transitions") or ["T1", "T2", "T3", "T5", "T6"]

    docs = await _parsed_docs(session, object_id, principal)
    provider = state.router.select(protected_object=obj.protected)
    run_id = f"run-{object_id}-{uuid.uuid4().hex[:8]}"
    result = await run_analysis(object_id, docs, object_card_for_rules(obj.card), state.norms,
                                state.rules_config, state.scoring_config, provider,
                                state.router.verifier(provider, obj.protected), run_id, chosen)

    session.add(AnalysisRun(id=run_id, object_id=object_id, transitions=chosen, status="done",
                            stage="готово", started_by=principal.subject,
                            finished_at=datetime.utcnow(), documents_count=len(docs),
                            findings_count=len(result["findings"]),
                            provenance={"provider": provider.name, "model": provider.model}))
    from api.seed import _finding_row
    for finding in result["findings"]:
        session.add(_finding_row(finding, run_id))
    _audit(session, principal, "analysis.run", "run", run_id,
           {"object_id": object_id, "transitions": chosen,
            "findings": len(result["findings"]), "provider": provider.name})
    await session.commit()
    return {"run_id": run_id, "status": "done", "stages": STAGES,
            "findings": len(result["findings"]),
            "attention": result["attention"]}


@router.get("/findings", summary="Журнал гипотез нарушений")
async def list_findings(object_id: str | None = None, severity: str | None = None,
                        transition: str | None = None, code: str | None = None,
                        session: AsyncSession = Depends(session_dep),
                        principal: Principal = Depends(permission("findings:read"))) -> dict:
    from api.rbac import scope_objects
    stmt = (select(FindingRow, ConstructionObject.name)
            .join(ConstructionObject, ConstructionObject.id == FindingRow.object_id))
    stmt = scope_objects(stmt, principal)
    if object_id:
        stmt = stmt.where(FindingRow.object_id == object_id)
    if severity:
        stmt = stmt.where(FindingRow.severity == severity)
    if transition:
        stmt = stmt.where(FindingRow.transition == transition)
    if code:
        stmt = stmt.where(FindingRow.code == code)
    rows = (await session.execute(stmt.order_by(FindingRow.priority_score.desc()))).all()

    ids = [row[0].id for row in rows]
    verdicts = await _verdicts(session, ids)
    return {"items": [_finding(row[0], row[1], verdicts.get(row[0].id)) for row in rows],
            "total": len(rows)}


@router.get("/findings/{finding_id}", summary="Карточка гипотезы")
async def get_finding(finding_id: str, session: AsyncSession = Depends(session_dep),
                      principal: Principal = Depends(permission("findings:read"))) -> dict:
    row, object_name = await _finding_or_denied(session, finding_id, principal)
    verdicts = await _verdicts(session, [finding_id])
    payload = _finding(row, object_name, verdicts.get(finding_id))
    payload["norm_texts"] = [
        {**ref, "text": (clause.text if (clause := state.norms.get(
            f"{ref['doc']}:{ref['clause']}")) else ""),
         "edition_check": state.norms.check_edition(f"{ref['doc']}:{ref['clause']}")}
        for ref in row.norm_refs]
    return payload


@router.post("/findings/{finding_id}/assess", summary="Оценка инспектора")
async def assess(finding_id: str, payload: dict, session: AsyncSession = Depends(session_dep),
                 principal: Principal = Depends(permission("findings:assess"))) -> dict:
    verdict = payload.get("verdict", "")
    if verdict not in ("confirmed", "rejected", "clarify"):
        raise HTTPException(status_code=422, detail="Недопустимая оценка гипотезы.")
    await _finding_or_denied(session, finding_id, principal)
    session.add(Assessment(id=str(uuid.uuid4()), finding_id=finding_id, verdict=verdict,
                           comment=payload.get("comment", ""), photos=payload.get("photos", []),
                           user_id=principal.subject))
    _audit(session, principal, "finding.assess", "finding", finding_id, {"verdict": verdict})
    await session.commit()
    return {"finding_id": finding_id, "verdict": verdict, "assessed_by": principal.full_name}


@router.get("/attention", summary="Карта внимания")
async def attention(object_id: str, session: AsyncSession = Depends(session_dep),
                    principal: Principal = Depends(permission("findings:read"))) -> dict:
    obj = await _object_or_denied(session, object_id, principal)
    rows = (await session.execute(
        select(FindingRow).where(FindingRow.object_id == object_id)
        .order_by(FindingRow.priority_score.desc()))).scalars().all()
    findings = [Finding.model_validate(_to_finding_dict(r)) for r in rows]
    return {"object": {"id": obj.id, "name": obj.name, "address": obj.address,
                       "contractor": obj.contractor, "stage": obj.stage},
            "items": build_attention_map(findings),
            "generated_at": datetime.utcnow().isoformat(timespec="seconds")}


@router.get("/diff", summary="Сравнение двух листов")
async def diff(document_before: str, document_after: str,
               session: AsyncSession = Depends(session_dep),
               principal: Principal = Depends(permission("objects:read"))) -> dict:
    from api.routers.objects import _document_or_denied
    before = await _document_or_denied(session, document_before, principal)
    after = await _document_or_denied(session, document_after, principal)
    for doc in (before, after):
        if not Path(doc.path).exists():
            raise HTTPException(status_code=404, detail="Файл документа недоступен.")
    regions = RasterDiffEngine().compare(Path(before.path).read_bytes(),
                                         Path(after.path).read_bytes())
    return {"engine": "raster", "before": before.id, "after": after.id,
            "regions": [r.__dict__ for r in regions]}


@router.post("/reports/{kind}", summary="Проект акта или предписания")
async def report(kind: str, payload: dict, session: AsyncSession = Depends(session_dep),
                 principal: Principal = Depends(permission("reports:build"))) -> Response:
    """Формируется проект документа. Юридически значимых действий система не совершает."""
    if kind not in ("act", "prescription"):
        raise HTTPException(status_code=404, detail="Неизвестный вид документа.")
    object_id = payload.get("object_id", "")
    obj = await _object_or_denied(session, object_id, principal)
    finding_ids = payload.get("finding_ids") or []
    rows = (await session.execute(
        select(FindingRow).where(FindingRow.object_id == object_id,
                                 FindingRow.id.in_(finding_ids)))).scalars().all()
    if not rows:
        raise HTTPException(status_code=422,
                            detail="Выберите хотя бы одну подтверждённую гипотезу.")
    findings = [Finding.model_validate(_to_finding_dict(r)) for r in rows]
    inspector = {"full_name": principal.full_name, "position": principal.position}
    card = {**obj.card, "name": obj.name, "address": obj.address}
    if kind == "act":
        data = build_inspection_act(card, findings, inspector, object_id, state.region_config)
        filename = f"proekt_akta_{object_id}.docx"
    else:
        deadline = date.today() + timedelta(days=int(payload.get("deadline_days", 30)))
        data = build_prescription(card, findings, inspector, object_id, deadline,
                                  state.region_config)
        filename = f"proekt_predpisaniya_{object_id}.docx"
    _audit(session, principal, f"report.{kind}", "object", object_id,
           {"findings": len(findings)})
    await session.commit()
    return Response(content=data, media_type=(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def _parsed_docs(session: AsyncSession, object_id: str, principal: Principal):
    rows = (await session.execute(
        select(DocumentRow).where(DocumentRow.object_id == object_id))).scalars().all()
    entries = [(Path(r.path), r.title) for r in rows if r.path and Path(r.path).exists()]
    if not entries:
        raise HTTPException(status_code=422, detail="Для объекта не загружено ни одного файла.")
    return parse_documents(entries, object_id)


async def _finding_or_denied(session: AsyncSession, finding_id: str, principal: Principal):
    from api.rbac import scope_objects
    stmt = (select(FindingRow, ConstructionObject.name)
            .join(ConstructionObject, ConstructionObject.id == FindingRow.object_id)
            .where(FindingRow.id == finding_id))
    row = (await session.execute(scope_objects(stmt, principal))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Гипотеза не найдена или недоступна.")
    return row[0], row[1]


async def _verdicts(session: AsyncSession, finding_ids: list[str]) -> dict:
    if not finding_ids:
        return {}
    rows = (await session.execute(
        select(Assessment).where(Assessment.finding_id.in_(finding_ids))
        .order_by(Assessment.created_at))).scalars().all()
    return {r.finding_id: {"verdict": r.verdict, "comment": r.comment,
                           "user_id": r.user_id,
                           "created_at": r.created_at.isoformat()} for r in rows}


def _finding(row: FindingRow, object_name: str, assessment: dict | None) -> dict:
    return {**_to_finding_dict(row), "object_name": object_name, "assessment": assessment}


def _to_finding_dict(row: FindingRow) -> dict:
    return {"id": row.id, "public_id": row.public_id, "object_id": row.object_id,
            "transition": row.transition, "code": row.code, "severity": row.severity,
            "confidence": row.confidence, "priority_score": row.priority_score,
            "title": row.title, "structural_element": row.structural_element,
            "evidence": row.evidence, "norm_refs": row.norm_refs,
            "legal_refs": row.legal_refs, "field_check": row.field_check,
            "documents_to_request": row.documents_to_request,
            "verification": row.verification, "injection_suspected": row.injection_suspected,
            "detector": row.detector, "provenance": row.provenance,
            "created_at": row.created_at.isoformat()}


def _audit(session: AsyncSession, principal: Principal, action: str, entity_type: str,
           entity_id: str, payload: dict | None = None) -> None:
    record(session, principal.subject, principal.roles[0] if principal.roles else "",
           action, entity_type, entity_id, payload or {})
