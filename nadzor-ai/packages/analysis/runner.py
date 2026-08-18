"""Сквозной прогон анализа: файлы → состояния → переходы → находки."""
from __future__ import annotations

from pathlib import Path

from analysis.attention import build_attention_map
from analysis.models import AnalysisContext, DocumentSet, Finding, ParsedDoc
from analysis.pipeline import build_states, load_document
from analysis.transitions import analyze_all

DEFAULT_TRANSITIONS = ["T1", "T2", "T3", "T5", "T6"]

# Этапы обработки для честного прогресса в интерфейсе.
STAGES = ["распознавание", "извлечение сущностей", "сравнение", "сверка с нормативами",
          "перепроверка", "готово"]


def parse_documents(entries: list[tuple[Path, str]], object_id: str) -> list[ParsedDoc]:
    """Разобрать файлы комплекта и извлечь факты."""
    return [load_document(path, object_id, title) for path, title in entries]


async def run_analysis(object_id: str, docs: list[ParsedDoc], object_card: dict, norms,
                       rules_config: dict, scoring_config: dict, provider, verifier,
                       run_id: str, transitions: list[str] | None = None) -> dict:
    """Выполнить переходы и собрать результат."""
    states: list[DocumentSet] = build_states(docs, object_id)
    ctx = AnalysisContext(
        object_id=object_id, run_id=run_id, norms=norms, rules_config=rules_config,
        scoring_config=scoring_config, object_card=dict(object_card), provider=provider,
        verifier=verifier, protected=bool(object_card.get("protected")),
    )
    findings: list[Finding] = await analyze_all(states, transitions or DEFAULT_TRANSITIONS, ctx)
    return {"states": states, "findings": findings,
            "attention": build_attention_map(findings), "run_id": run_id}


def object_card_for_rules(obj: dict) -> dict:
    """Карточка объекта в виде, ожидаемом правилами (даты как в документах)."""
    def ru(iso: str) -> str:
        if not iso or "-" not in iso:
            return iso
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"

    expertise = obj.get("expertise", {}) or {}
    return {
        "permit_date_ru": ru(obj.get("permit_date", "")),
        "expertise_date_ru": ru(expertise.get("date", "")),
        "contractor": obj.get("contractor", ""),
        "contractor_history": obj.get("contractor_history", 0.6),
        "seq_prefix": str(obj.get("id", "1"))[-1],
        "protected": obj.get("protected", False),
    }
