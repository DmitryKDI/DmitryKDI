"""Семантический слой на языковой модели.

Модель получает извлечённые факты и цитаты из нормативной базы, а текст
документов — только внутри недоверенного контейнера. Свободная генерация
запрещена: ответ обязан пройти валидацию по строгой схеме, иначе отбрасывается.
"""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from documents.schemas import Fact
from llm_core.envelope import SYSTEM_RULES
from llm_core.ports import CompletionRequest
from llm_core.schemas import LLMFindings, SchemaViolation, parse_strict

MAX_TEXT_BLOCKS = 24
MAX_STRUCTURED_FACTS = 120

INSTRUCTION = (
    "Сравни состояния «до» и «после». Найди смысловые расхождения, которые не сводятся "
    "к формальной проверке: отступления от проектных решений, противоречия в указаниях, "
    "несоответствия нормативным требованиям. Для каждого расхождения укажи код из "
    "классификатора, идентификаторы фактов и пункт норматива. Если расхождений нет, "
    "верни пустой список findings."
)


def _side_facts(state: DocumentSet | None, side: str) -> tuple[list[dict], list[str], dict]:
    """Структурированные факты, недоверенные текстовые блоки и указатель на факты."""
    structured: list[dict] = []
    blocks: list[str] = []
    index: dict[str, tuple[Fact, object]] = {}
    if state is None:
        return structured, blocks, index
    for doc in state.docs:
        for fact in doc.facts:
            index[fact.id] = (fact, doc)
            if fact.fact_type == "text":
                if len(blocks) < MAX_TEXT_BLOCKS:
                    blocks.append(f"[факт {fact.id} | сторона: {side} | документ: {doc.title}]\n"
                                  f"{fact.value}")
            elif len(structured) < MAX_STRUCTURED_FACTS:
                structured.append({"id": fact.id, "side": side, "type": fact.fact_type,
                                   "key": fact.key, "value": fact.value,
                                   "document": doc.title})
    return structured, blocks, index


async def run_llm_layer(before: DocumentSet | None, after: DocumentSet | None,
                        transition: str, ctx) -> list[Detection]:
    """Запустить семантический слой. Ошибка провайдера не роняет анализ."""
    if ctx.provider is None or not ctx.rules_config.get("llm_layer", {}).get("enabled", True):
        return []
    facts_before, blocks_before, index_before = _side_facts(before, "before")
    facts_after, blocks_after, index_after = _side_facts(after, "after")
    index = {**index_before, **index_after}
    if not blocks_before and not blocks_after:
        return []

    clauses = ctx.norms.search("отступление рабочей документации от проектных решений "
                               "армирование класс бетона", top_k=5)
    req = CompletionRequest(
        task="semantic_diff",
        system=SYSTEM_RULES,
        facts=facts_before + facts_after,
        norm_clauses=[c.to_norm_ref() | {"text": c.text} for c in clauses],
        untrusted_blocks=blocks_before + blocks_after,
        prompt_version="2.3",
        context={"instruction": INSTRUCTION, "transition": transition},
    )
    retries = int(ctx.rules_config.get("llm_layer", {}).get("schema_retries", 2))
    parsed = await _complete_with_schema(ctx.provider, req, retries)
    if parsed is None:
        return []
    return _to_detections(parsed, index, ctx)


async def _complete_with_schema(provider, req: CompletionRequest, retries: int):
    """Получить ответ, прошедший валидацию по схеме. Иначе — отказ."""
    for _ in range(max(retries, 1)):
        try:
            response = await provider.complete(req)
            return parse_strict(response.raw_text, LLMFindings)
        except SchemaViolation:
            continue
        except Exception:      # noqa: BLE001 — недоступность провайдера не роняет анализ
            return None
    return None


def _to_detections(parsed: LLMFindings, index: dict, ctx) -> list[Detection]:
    """Превратить вывод модели в находки, отбрасывая ссылки на несуществующие факты."""
    out: list[Detection] = []
    for item in parsed.findings:
        evidence = []
        for fact_id in item.fact_ids:
            pair = index.get(fact_id)
            if pair is None:
                continue                      # модель сослалась на несуществующий факт
            fact, doc = pair
            evidence.append(Evidence.from_fact(fact, doc, "факт"))
        if not evidence:
            continue                          # вывод без доказательной базы не сохраняется
        if ctx.norms.get(item.norm_clause) is None:
            continue                          # вывод со ссылкой на несуществующий пункт
        out.append(Detection(
            code=item.code, title=item.title, evidence=evidence,
            element=item.element.model_dump(), severity=item.severity,
            confidence=item.confidence, norm_refs=[item.norm_clause],
            field_check=_field_check(item.element.model_dump()),
            documents_to_request=["Рабочая документация с отметкой «в производство работ»",
                                  "Согласование проектной организации на отступление"],
            detector="llm"))
    return out


def _field_check(element: dict) -> str:
    mark = element.get("mark") or "конструкции"
    return (f"Контроль фактического исполнения {mark}, отм. {element.get('level', '')}, "
            f"оси {element.get('axes', '')}: вскрытие либо неразрушающий контроль")
