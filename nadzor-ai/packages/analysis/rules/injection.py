"""Обнаружение попыток воздействия на систему анализа через документ (У-1).

Срабатывание не блокирует анализ: оно становится отдельной находкой и поводом
проверить добросовестность лица, представившего документацию.
"""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.common import rule_enabled
from llm_core.guard import scan_facts


def check_injection(before: DocumentSet | None, after: DocumentSet | None,
                    ctx) -> list[Detection]:
    """Проверить документы обоих состояний на попытки внушения инструкций."""
    if not rule_enabled(ctx.rules_config, "injection_attempt"):
        return []
    found: list[Detection] = []
    for state in (before, after):
        if state is None:
            continue
        for doc in state.docs:
            found.extend(_check_doc(doc))
    return found


def _check_doc(doc) -> list[Detection]:
    facts = [f.model_dump() for f in doc.facts]
    hits = scan_facts(facts)
    if not hits:
        return []
    by_id = {f.id: f for f in doc.facts}
    anchor = by_id.get(hits[0].fact_id) or (doc.facts[0] if doc.facts else None)
    if anchor is None:
        return []
    labels = sorted({h.label for h in hits})
    return [Detection(
        code="D-90",
        title=f"В документе «{doc.title}» обнаружена попытка воздействия на систему анализа: "
              + "; ".join(labels),
        evidence=[Evidence.from_fact(anchor, doc, "факт", hits[0].excerpt[:200])],
        element={"name": "Документ", "mark": "—", "level": "—", "axes": "—", "section": "—"},
        confidence=0.99, norm_refs=["ГрК РФ:53"],
        field_check="Проверить подлинность документа и его соответствие оригиналу на бумажном "
                    "носителе; запросить оригинал у изготовителя",
        documents_to_request=["Оригинал документа о качестве",
                              "Пояснения лица, представившего документацию"],
        detector="injection.attempt", injection_suspected=True)]
