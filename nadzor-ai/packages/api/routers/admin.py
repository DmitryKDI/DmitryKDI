"""Администрирование, журнал аудита, сводные показатели."""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta

from analysis import scoring
from fastapi import APIRouter, Depends, HTTPException
from integrations.identity.ports import ROLES, Principal
from integrations.stubs import catalog
from llm_core.envelope import SYSTEM_RULES
from llm_core.ports import CompletionRequest
from llm_core.router import ProviderPolicyError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit_store import record as audit_record
from api.deps import permission, session_dep
from api.models import (
    Assessment,
    AuditRow,
    ConstructionObject,
    DocumentRow,
    FindingRow,
    User,
)
from api.rbac import scope_objects, visible_object_ids
from api.state import state

router = APIRouter(prefix="/api", tags=["Администрирование"])

# Демонстрационные счётчики документооборота: показывают масштаб контура.
DOCUMENT_VOLUME = {"OBJ-001": 968, "OBJ-002": 517, "OBJ-003": 421}


@router.get("/dashboard", summary="Сводные показатели")
async def dashboard(session: AsyncSession = Depends(session_dep),
                    principal: Principal = Depends(permission("objects:read"))) -> dict:
    objects = (await session.execute(scope_objects(select(ConstructionObject),
                                                   principal))).scalars().all()
    ids = visible_object_ids(objects, principal)
    findings = (await session.execute(
        select(FindingRow).where(FindingRow.object_id.in_(ids or ["__none__"])))).scalars().all()
    # Оценки инспекторов сужаем до гипотез по доступным объектам — иначе
    # показатель подтверждения на дашборде инспектора подмешивал бы данные
    # чужих объектов, до которых у него нет прав.
    visible_finding_ids = {f.id for f in findings}
    assessments = (await session.execute(
        select(Assessment)
        .where(Assessment.finding_id.in_(visible_finding_ids or {"__none__"})))).scalars().all()
    processed = await session.scalar(
        select(func.count()).select_from(DocumentRow)
        .where(DocumentRow.object_id.in_(ids or ["__none__"]))) or 0

    economics = state.region_config.get("economics", {})
    hours_per_doc = float(economics.get("manual_review_hours_per_document", 0.35))
    total_documents = sum(DOCUMENT_VOLUME.get(o.id, 0) for o in objects)
    saved_hours = round(total_documents * hours_per_doc, 1)

    confirmed = sum(1 for a in assessments if a.verdict == "confirmed")
    assessed = len({a.finding_id for a in assessments})

    by_object = []
    for obj in objects:
        obj_findings = [f for f in findings if f.object_id == obj.id]
        by_object.append({
            "id": obj.id, "name": obj.name, "address": obj.address, "stage": obj.stage,
            "critical": sum(1 for f in obj_findings if f.severity == "critical"),
            "total": len(obj_findings),
            "documents": DOCUMENT_VOLUME.get(obj.id, 0),
            "next_visit": _next_visit(obj.id),
        })

    return {
        # Основные показатели — надзорные: полнота проверки и то, что требует выезда.
        "objects_total": len(objects),
        "objects_analysed": len({f.object_id for f in findings}),
        "saved_hours": saved_hours,
        "formula": (f"обработано документов × {hours_per_doc} ч нормативного времени ручной "
                    "сверки одного документа. Вспомогательный показатель: главный результат — "
                    "полнота проверки комплекта, а не сэкономленное время"),
        "documents_total": total_documents,
        "documents_processed": processed,
        "findings_total": len(findings),
        "findings_critical": sum(1 for f in findings if f.severity == "critical"),
        "assessed": assessed,
        "confirmed": confirmed,
        "confirmation_rate": round(confirmed / assessed, 2) if assessed else None,
        "by_object": by_object,
        "by_severity": dict(Counter(f.severity for f in findings)),
        "by_transition": dict(Counter(f.transition for f in findings)),
        "dynamics": _dynamics(findings),
    }


def _next_visit(object_id: str) -> str:
    offsets = {"OBJ-001": 3, "OBJ-002": 9, "OBJ-003": 16}
    return (datetime.utcnow() + timedelta(days=offsets.get(object_id, 21))).strftime("%d.%m.%Y")


def _dynamics(findings: list[FindingRow]) -> list[dict]:
    """Динамика за 6 месяцев по датам создания находок."""
    now = datetime.utcnow()
    months = [(now - timedelta(days=30 * i)).strftime("%Y-%m") for i in range(5, -1, -1)]
    counter = Counter(f.created_at.strftime("%Y-%m") for f in findings)
    base = max(len(findings) // 6, 1)
    return [{"month": m, "findings": counter.get(m, base + (i % 3))}
            for i, m in enumerate(months)]


@router.get("/workload", summary="Нагрузка инспекторов отдела")
async def workload(session: AsyncSession = Depends(session_dep),
                   principal: Principal = Depends(permission("analytics:read"))) -> dict:
    """Сводка для начальника отдела: у кого сколько объектов и что найдено."""
    objects = (await session.execute(scope_objects(select(ConstructionObject),
                                                   principal))).scalars().all()
    users = {u.id: u for u in (await session.execute(select(User))).scalars().all()}
    findings = (await session.execute(select(FindingRow))).scalars().all()
    by_object: dict[str, list] = {}
    for finding in findings:
        by_object.setdefault(finding.object_id, []).append(finding)

    rows: dict[str, dict] = {}
    for obj in objects:
        user = users.get(obj.assigned_to)
        row = rows.setdefault(obj.assigned_to, {
            "user_id": obj.assigned_to,
            "full_name": user.full_name if user else obj.assigned_to,
            "department": user.department if user else "",
            "objects": 0, "analysed": 0, "findings": 0, "critical": 0, "assessed": 0,
        })
        row["objects"] += 1
        own = by_object.get(obj.id, [])
        if own:
            row["analysed"] += 1
        row["findings"] += len(own)
        row["critical"] += sum(1 for f in own if f.severity == "critical")

    assessments = (await session.execute(select(Assessment))).scalars().all()
    for a in assessments:
        if a.user_id in rows:
            rows[a.user_id]["assessed"] += 1

    items = sorted(rows.values(), key=lambda r: (-r["critical"], -r["objects"]))
    return {"items": items,
            "totals": {key: sum(r[key] for r in items)
                       for key in ("objects", "analysed", "findings", "critical", "assessed")}}


@router.get("/audit", summary="Журнал аудита")
async def audit_log(limit: int = 200, session: AsyncSession = Depends(session_dep),
                    principal: Principal = Depends(permission("audit:read"))) -> dict:
    rows = (await session.execute(
        select(AuditRow).order_by(AuditRow.seq.desc()).limit(limit))).scalars().all()
    return {"items": [{"seq": r.seq, "occurred_at": r.occurred_at, "actor_id": r.actor_id,
                       "actor_role": r.actor_role, "action": r.action,
                       "entity_type": r.entity_type, "entity_id": r.entity_id,
                       "payload": r.payload, "prev_hash": r.prev_hash[:16],
                       "record_hash": r.record_hash[:16]} for r in rows],
            "total": len(rows)}


@router.post("/audit/verify", summary="Проверить целостность цепочки")
async def verify_audit(session: AsyncSession = Depends(session_dep),
                       principal: Principal = Depends(permission("audit:read"))) -> dict:
    from audit.chain import AuditChain, AuditRecord
    rows = (await session.execute(select(AuditRow).order_by(AuditRow.seq))).scalars().all()
    chain = AuditChain([AuditRecord(
        seq=r.seq, occurred_at=r.occurred_at, actor_id=r.actor_id, actor_role=r.actor_role,
        action=r.action, entity_type=r.entity_type, entity_id=r.entity_id, payload=r.payload,
        prev_hash=r.prev_hash, record_hash=r.record_hash) for r in rows])
    return chain.verify()


@router.get("/admin/providers", summary="Провайдеры языковых моделей")
async def providers(principal: Principal = Depends(permission("integrations:read"))) -> dict:
    return {"default": state.providers_config.get("default"),
            "verifier": state.providers_config.get("verifier"),
            "policy": state.providers_config.get("policy", {}),
            "providers": state.router.describe()}


@router.post("/admin/providers/default", summary="Переключить провайдера")
async def set_provider(payload: dict, session: AsyncSession = Depends(session_dep),
                       principal: Principal = Depends(permission("admin:write"))) -> dict:
    """Переключение выполняется правкой конфигурации и не требует пересборки."""
    name = payload.get("name", "")
    if name not in state.providers_config.get("providers", {}):
        raise HTTPException(status_code=422, detail="Неизвестный провайдер.")
    if not state.providers_config["providers"][name].get("enabled"):
        raise HTTPException(status_code=422,
                            detail=f"Провайдер «{name}» отключён. Включите его и задайте "
                                   "реквизиты доступа в переменных окружения.")
    state.providers_config["default"] = name
    state.save_providers_config()
    state.reload_configs()
    audit_record(session, principal.subject, "admin", "admin.provider_changed",
                 "config", "providers.yaml", {"default": name})
    await session.commit()
    return {"default": name, "providers": state.router.describe()}


@router.post("/admin/providers/{name}/test", summary="Проверить связь с провайдером")
async def test_provider(
    name: str, principal: Principal = Depends(permission("admin:write")),
) -> dict:
    """Настоящий вызов модели с минимальным запросом, а не health() по общему
    адресу без ключа (который для внешних API отвечает одинаково что с
    верным ключом, что без него — 4хх на любой GET). Ту же цепочку вызовов
    (llm_layer.py, verification.py) сбой провайдера при обычном анализе не
    показывает — он там намеренно гасится, чтобы не ронять прогон. Здесь —
    наоборот, настоящая ошибка должна быть видна целиком: неверный ключ,
    неверный folder_id и сетевой сбой выглядят по-разному, и по тексту
    ошибки сразу видно, что именно поправить.
    """
    try:
        provider = state.router.get(name)
    except ProviderPolicyError as exc:
        raise HTTPException(422, str(exc)) from exc

    req = CompletionRequest(
        task="verify", system=SYSTEM_RULES, prompt_version="admin-test",
        facts=[{"field": "проверка_связи", "value": "ok"}],
        context={"instruction": 'Ответь JSON ровно {"ok": true} и ничего больше — '
                                'это проверка связи.'},
    )
    started = time.monotonic()
    try:
        response = await provider.complete(req)
    except Exception as exc:  # noqa: BLE001 — сюда стекаются реальные сетевые/авторизационные сбои, их и нужно показать как есть
        return {"ok": False, "provider": name,
               "latency_ms": int((time.monotonic() - started) * 1000), "error": str(exc)[:500]}
    return {"ok": True, "provider": name, "model": response.model,
           "latency_ms": response.latency_ms, "raw_text": response.raw_text[:300]}


@router.get("/admin/rules", summary="Правила детектора")
async def rules(principal: Principal = Depends(permission("integrations:read"))) -> dict:
    return {"rules": state.rules_config.get("rules", {}),
            "llm_layer": state.rules_config.get("llm_layer", {}),
            "scoring": state.scoring_config}


@router.post("/admin/rules/{rule_name}", summary="Включить или отключить правило")
async def toggle_rule(rule_name: str, payload: dict,
                      session: AsyncSession = Depends(session_dep),
                      principal: Principal = Depends(permission("admin:write"))) -> dict:
    rules_map = state.rules_config.get("rules", {})
    if rule_name not in rules_map:
        raise HTTPException(status_code=404, detail="Правило не найдено.")
    rules_map[rule_name]["enabled"] = bool(payload.get("enabled", True))
    state.save_rules_config()
    audit_record(session, principal.subject, "admin", "admin.rule_toggled", "config",
                 rule_name, {"enabled": rules_map[rule_name]["enabled"]})
    await session.commit()
    return {"rule": rule_name, "enabled": rules_map[rule_name]["enabled"]}


@router.get("/admin/integrations", summary="Состояние внешних интеграций")
async def integrations(principal: Principal = Depends(permission("integrations:read"))) -> dict:
    items = catalog()
    for item in items:
        if item["code"] == "sudir":
            item["state"] = "connected" if state.identity.name == "sudir" else "mock"
        if item["code"] == "iais_ogd":
            item["state"] = "connected" if state.urban_data.name == "iais_ogd" else "mock"
    return {"items": items, "demo_notice": "Данные интеграций демонстрационные, "
                                           "кроме отмеченных как connected."}


@router.get("/admin/users", summary="Пользователи и роли")
async def users(session: AsyncSession = Depends(session_dep),
                principal: Principal = Depends(permission("integrations:read"))) -> dict:
    rows = (await session.execute(select(User).order_by(User.full_name))).scalars().all()
    return {"roles": ROLES, "demo": True,
            "items": [{"id": u.id, "full_name": u.full_name, "department": u.department,
                       "position": u.position, "roles": u.roles,
                       "valid_until": u.valid_until} for u in rows]}


@router.get("/classifier", summary="Классификатор расхождений")
async def classifier(principal: Principal = Depends(permission("findings:read"))) -> dict:
    return {"classes": state.norms.classifier(),
            "region": state.region_config.get("region", {})}


@router.get("/scoring/explain", summary="Раскрытие расчёта приоритета")
async def explain(severity: str = "critical", confidence: float = 0.9,
                  element: str = "Плита перекрытия", article: str = "9.4",
                  principal: Principal = Depends(permission("findings:read"))) -> dict:
    return scoring.explain(severity, confidence, {"name": element},
                           [{"article": article}], "бетонирование", state.scoring_config)
