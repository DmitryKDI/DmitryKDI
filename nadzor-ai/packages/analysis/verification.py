"""Перепроверка вывода вторым провайдером (мера Б.3.7).

Расхождение выводов при одинаковых входных данных понижает уверенность и
поднимает флаг «требует внимания эксперта».
"""
from __future__ import annotations

from llm_core.envelope import SYSTEM_RULES
from llm_core.ports import CompletionRequest
from llm_core.schemas import LLMVerdict, SchemaViolation, parse_strict

INSTRUCTION = ("Проверь обоснованность вывода. Ответь JSON с полями agreement (bool), "
               "confidence (0..1) и comment. Согласие возможно только при наличии "
               "доказательной базы и ссылки на пункт норматива.")


def deterministic_verification() -> dict:
    """Детерминированные правила перепроверке моделью не подлежат."""
    return {"status": "deterministic", "provider": "rules", "agreement": True,
            "comment": "Вывод получен детерминированным правилом и воспроизводим."}


async def verify_detection(detection, ctx) -> dict:
    """Перепроверить вывод LLM-слоя вторым провайдером."""
    if detection.detector != "llm":
        return deterministic_verification()
    if ctx.verifier is None:
        return {"status": "skipped", "provider": "", "agreement": None,
                "comment": "Провайдер перепроверки не настроен."}
    payload = {"code": detection.code, "title": detection.title,
               "norm_clause": detection.norm_refs[0] if detection.norm_refs else "",
               "fact_ids": [e.doc_id for e in detection.evidence]}
    req = CompletionRequest(
        task="verify", system=SYSTEM_RULES, prompt_version="1.2",
        context={"instruction": INSTRUCTION, "finding": payload},
    )
    try:
        response = await ctx.verifier.complete(req)
        verdict = parse_strict(response.raw_text, LLMVerdict)
    except (SchemaViolation, Exception):      # noqa: B014 — любая ошибка означает «не проверено»
        return {"status": "failed", "provider": getattr(ctx.verifier, "name", ""),
                "agreement": None, "comment": "Перепроверка не выполнена."}
    return {"status": "confirmed" if verdict.agreement else "disputed",
            "provider": getattr(ctx.verifier, "name", ""),
            "agreement": verdict.agreement, "confidence": verdict.confidence,
            "comment": verdict.comment}


def apply_verification(confidence: float, verification: dict) -> tuple[float, bool]:
    """Скорректировать уверенность по результату перепроверки."""
    if verification.get("status") == "disputed":
        return round(confidence * 0.6, 2), True
    if verification.get("status") in ("failed", "skipped"):
        return round(confidence * 0.9, 2), False
    return confidence, False
