"""Карта внимания: 5–7 позиций, отвечающих на вопрос «куда идти на объекте»."""
from __future__ import annotations

from analysis.models import Finding

MAX_ITEMS = 7


def build_attention_map(findings: list[Finding], limit: int = MAX_ITEMS) -> list[dict]:
    """Собрать карту внимания из гипотез: по одной позиции на конструктив."""
    ranked = sorted(findings, key=lambda f: -f.priority_score)
    seen: set[str] = set()
    items: list[dict] = []
    for finding in ranked:
        element = finding.structural_element or {}
        key = f"{element.get('mark', '')}|{element.get('level', '')}|{element.get('axes', '')}"
        if key in seen:
            continue
        seen.add(key)
        items.append(_item(finding, element))
        if len(items) >= limit:
            break
    return items


def _item(finding: Finding, element: dict) -> dict:
    return {
        "finding_id": finding.id,
        "public_id": finding.public_id,
        "priority_score": finding.priority_score,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "element": {
            "name": element.get("name", ""),
            "mark": element.get("mark", ""),
            "level": element.get("level", ""),
            "axes": element.get("axes", ""),
            "section": element.get("section", ""),
        },
        "location": _location(element),
        "summary": finding.title,
        "what_to_check": finding.field_check,
        "documents_to_take": finding.documents_to_request,
        "norm_refs": finding.norm_refs,
        "checked": False,
    }


def _location(element: dict) -> str:
    parts = [element.get("mark", ""), f"оси {element['axes']}" if element.get("axes") else "",
             f"отм. {element['level']}" if element.get("level") else "",
             element.get("section", "")]
    return ", ".join(p for p in parts if p and p not in ("—", "оси —", "отм. —"))
