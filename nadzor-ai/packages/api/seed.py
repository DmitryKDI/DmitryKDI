"""Наполнение базы демонстрационными данными.

Комплект разбирается и анализируется один раз при первом запуске; повторный
запуск данные не пересоздаёт.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from analysis.runner import object_card_for_rules, parse_documents, run_analysis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit_store import record as audit_record
from api.models import AnalysisRun, ConstructionObject, DocumentRow, FindingRow, User
from api.state import MANIFEST, ROOT, state


async def is_seeded(session: AsyncSession) -> bool:
    total = await session.scalar(select(func.count()).select_from(ConstructionObject))
    return bool(total)


async def seed(session: AsyncSession) -> dict:
    """Разобрать демо-комплект, выполнить анализ и сохранить результат."""
    if await is_seeded(session):
        return {"seeded": False}
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for person in manifest["staff"]:
        session.add(User(id=person["subject"], full_name=person["full_name"],
                         department=person.get("department", ""),
                         position=person.get("position", ""), roles=person.get("roles", []),
                         valid_until=person.get("valid_until", "")))

    stats = {"objects": 0, "documents": 0, "findings": 0}
    for obj in manifest["objects"]:
        session.add(_object_row(obj))
        entries = [m for m in manifest["documents"] if m["object_id"] == obj["id"]]
        docs = parse_documents([(ROOT / e["file"], e["title"]) for e in entries], obj["id"])
        for doc in docs:
            session.add(DocumentRow(
                id=doc.id, object_id=obj["id"], state_kind=doc.state_kind.value,
                doc_kind=doc.doc_kind.value, title=doc.title, path=doc.path,
                revision=doc.revision, doc_date=doc.doc_date, page_count=doc.page_count,
                sha256=doc.file_id, signature_status="valid", facts_count=len(doc.facts)))

        provider = state.router.select(protected_object=obj.get("protected", False))
        run_id = f"run-{obj['id']}-{uuid.uuid4().hex[:8]}"
        result = await run_analysis(
            obj["id"], docs, object_card_for_rules(obj), state.norms,
            state.rules_config, state.scoring_config, provider,
            state.router.verifier(provider, obj.get("protected", False)), run_id)

        session.add(AnalysisRun(
            id=run_id, object_id=obj["id"], transitions=["T1", "T2", "T3", "T5", "T6"],
            status="done", stage="готово", started_by=obj["assigned_to"],
            finished_at=datetime.utcnow(), documents_count=len(docs),
            findings_count=len(result["findings"]),
            provenance={"provider": provider.name, "model": provider.model}))
        for finding in result["findings"]:
            session.add(_finding_row(finding, run_id))

        stats["objects"] += 1
        stats["documents"] += len(docs)
        stats["findings"] += len(result["findings"])

        audit_record(session, "system", "admin", "analysis.completed", "run", run_id,
                     {"object_id": obj["id"], "findings": len(result["findings"]),
                      "provider": provider.name})

    await session.commit()
    return {"seeded": True, **stats}


def _object_row(obj: dict) -> ConstructionObject:
    return ConstructionObject(
        id=obj["id"], permit_number=obj["permit_number"], permit_date=obj["permit_date"],
        name=obj["name"], address=obj["address"], district=obj.get("district", ""),
        cadastral_number=obj.get("cadastral_number", ""), developer=obj["developer"],
        contractor=obj["contractor"], designer=obj["designer"], stage=obj.get("stage", ""),
        planned_completion=obj.get("planned_completion", ""), card=obj,
        data_source="iais_ogd", department=obj["department"],
        assigned_to=obj["assigned_to"], protected=obj.get("protected", False))


def _finding_row(finding, run_id: str) -> FindingRow:
    data = finding.model_dump(mode="json")
    return FindingRow(
        id=data["id"], public_id=data["public_id"], run_id=run_id,
        object_id=data["object_id"], transition=data["transition"], code=data["code"],
        severity=data["severity"], confidence=data["confidence"],
        priority_score=data["priority_score"], title=data["title"],
        structural_element=data["structural_element"], evidence=data["evidence"],
        norm_refs=data["norm_refs"], legal_refs=data["legal_refs"],
        field_check=data["field_check"], documents_to_request=data["documents_to_request"],
        verification=data["verification"], injection_suspected=data["injection_suspected"],
        detector=data["detector"], provenance=data["provenance"])
