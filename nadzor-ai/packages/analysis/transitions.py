"""Единый движок переходов: один контракт, семь типов перехода.

Тип перехода выбирает набор правил из реестра, но не меняет архитектуру:
на входе два состояния, на выходе список находок.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from analysis import scoring
from analysis.llm_layer import run_llm_layer
from analysis.lifecycle import pick_states
from analysis.models import AnalysisContext, Detection, DocumentSet, Finding
from analysis.rules import (
    arithmetic,
    chronology,
    completeness,
    expertise,
    injection,
    materials,
    sro,
)
from analysis.verification import apply_verification, verify_detection

# Реестр правил по типам перехода. Словарь, а не иерархия классов.
RULE_REGISTRY = {
    "T1": [materials.compare_specifications, expertise.check_repeat_expertise,
           injection.check_injection],
    "T2": [expertise.check_remarks_addressed, injection.check_injection],
    "T3": [materials.compare_specifications, injection.check_injection],
    "T4": [materials.compare_specifications, injection.check_injection],
    "T5": [materials.check_as_built_materials, chronology.check_material_chronology,
           chronology.check_permit_chronology, completeness.check_test_reports,
           completeness.check_required_fields, arithmetic.check_volumes,
           sro.check_sro_membership, injection.check_injection],
    "T6": [chronology.check_material_chronology, completeness.check_required_fields,
           injection.check_injection],
    "T7": [injection.check_injection],
}

# Переходы, на которых имеет смысл семантический слой языковой модели.
LLM_TRANSITIONS = {"T1", "T3", "T4", "T5"}


async def analyze(transition: str, before: DocumentSet | None, after: DocumentSet | None,
                  ctx: AnalysisContext) -> list[Finding]:
    """Проанализировать один переход."""
    detections: list[Detection] = []
    for rule in RULE_REGISTRY.get(transition, []):
        detections.extend(rule(before, after, ctx))
    if transition in LLM_TRANSITIONS:
        detections.extend(await run_llm_layer(before, after, transition, ctx))

    findings: list[Finding] = []
    for detection in detections:
        verification = await verify_detection(detection, ctx)
        finding = _enrich(detection, transition, verification, ctx)
        if finding is not None:
            findings.append(finding)
    return findings


async def analyze_all(states: list[DocumentSet], transitions: list[str],
                      ctx: AnalysisContext) -> list[Finding]:
    """Проанализировать выбранные переходы и убрать повторы."""
    collected: list[Finding] = []
    for transition in transitions:
        before, after = pick_states(states, transition)
        if after is None and transition != "T7":
            continue
        collected.extend(await analyze(transition, before, after, ctx))
    unique = _deduplicate(collected)
    unique.sort(key=lambda f: (-f.priority_score, f.public_id))
    return unique


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    """Одно и то же расхождение, найденное на разных переходах, показывается один раз."""
    seen: dict[tuple, Finding] = {}
    for finding in findings:
        key = (finding.code, finding.title,
               tuple(sorted((e.doc_id, e.page) for e in finding.evidence)))
        if key not in seen:
            seen[key] = finding
    return list(seen.values())


def _enrich(detection: Detection, transition: str, verification: dict,
            ctx: AnalysisContext) -> Finding | None:
    """Дополнить находку нормативным основанием, приоритетом и происхождением.

    Находка без пункта норматива или без координат не создаётся (мера Б.6).
    """
    klass = ctx.norms.klass(detection.code)
    norm_keys = detection.norm_refs or [f"{r['doc']}:{r['clause']}" for r in klass.get("norm_refs", [])]
    norm_refs = ctx.norms.norm_refs(norm_keys)
    legal_keys = detection.legal_refs or [
        f"{r['article']}/{r.get('part', '')}" for r in klass.get("legal_refs", [])]
    legal_refs = ctx.norms.legal_refs(legal_keys)
    if not norm_refs or not detection.evidence:
        return None

    severity = detection.severity or klass.get("default_severity", "minor")
    confidence, needs_expert = apply_verification(detection.confidence, verification)
    threshold = ctx.rules_config.get("llm_layer", {}).get("low_confidence_threshold", 0.55)
    if confidence < threshold:
        needs_expert = True

    priority = scoring.score(severity, confidence, detection.element, legal_refs,
                             detection.title, ctx.scoring_config,
                             ctx.object_card.get("contractor_history", 0.5))
    public_id = f"H-{ctx.object_card.get('seq_prefix', '1')}{len(ctx.object_card.get('_issued', [])) + 1:03d}"
    ctx.object_card.setdefault("_issued", []).append(public_id)

    try:
        return Finding(
            id=str(uuid.uuid4()), public_id=public_id, object_id=ctx.object_id,
            transition=transition, code=detection.code, severity=severity,
            confidence=round(confidence, 2), priority_score=priority,
            title=detection.title, structural_element=detection.element,
            evidence=detection.evidence, norm_refs=norm_refs, legal_refs=legal_refs,
            field_check=detection.field_check,
            documents_to_request=detection.documents_to_request,
            verification={**verification, "needs_expert": needs_expert},
            injection_suspected=detection.injection_suspected,
            detector=detection.detector,
            provenance={
                "model": getattr(ctx.provider, "model", "rules"),
                "provider": getattr(ctx.provider, "name", "rules"),
                "prompt_version": "2.3" if detection.detector == "llm" else "—",
                "detector": detection.detector,
                "run_id": ctx.run_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
    except ValueError:
        return None      # находка без источника не сохраняется и не показывается
