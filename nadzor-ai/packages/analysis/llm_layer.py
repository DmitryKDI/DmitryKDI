"""Семантический слой на языковой модели.

Модель получает извлечённые факты и цитаты из нормативной базы, а текст
документов — только внутри недоверенного контейнера. Свободная генерация
запрещена: ответ обязан пройти валидацию по строгой схеме, иначе отбрасывается.
"""
from __future__ import annotations

from documents.schemas import Fact
from llm_core.envelope import SYSTEM_RULES
from llm_core.ports import CompletionRequest, ImageBlock
from llm_core.schemas import LLMFindings, SchemaViolationError, parse_strict

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.rooms import candidate_rooms, facts_by_room
from analysis.rules.vision_escalation import needs_escalation, render_room_images

# На комплекте без общих номеров помещений (например, ведомость конструкций)
# кандидатов для блокировки нет — предел остаётся плоским, как раньше.
MAX_TEXT_BLOCKS = 60
MAX_STRUCTURED_FACTS = 200

INSTRUCTION = (
    "Сравни состояния «до» и «после». Найди смысловые расхождения, которые не сводятся "
    "к формальной проверке: отступления от проектных решений, противоречия в указаниях, "
    "несоответствия нормативным требованиям. Для каждого расхождения укажи код из "
    "классификатора, идентификаторы фактов и пункт норматива. Если расхождений нет, "
    "верни пустой список findings."
)


def _priority_ids(grouped: dict[str, list[tuple[Fact, object]]]) -> frozenset[str]:
    return frozenset(fact.id for facts in grouped.values() for fact, _ in facts)


def _side_facts(state: DocumentSet | None, side: str,
                priority: frozenset[str] = frozenset()) -> tuple[list[dict], list[str], dict]:
    """Структурированные факты, недоверенные текстовые блоки и указатель на факты.

    Факты, отобранные блокировкой по номеру помещения, идут в LLM первыми —
    иначе на комплекте в сотни листов до них просто не дойдёт очередь под лимитом.
    """
    structured: list[dict] = []
    blocks: list[str] = []
    index: dict[str, tuple[Fact, object]] = {}
    if state is None:
        return structured, blocks, index
    pairs = [(fact, doc) for doc in state.docs for fact in doc.facts]
    if priority:
        pairs.sort(key=lambda pair: pair[0].id not in priority)
    for fact, doc in pairs:
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


def _vision_images(rooms: set[str], grouped_before: dict[str, list], grouped_after: dict[str, list],
                   ctx) -> list[ImageBlock]:
    """Листы для кандидатов, где текста рядом с помещением недостаточно.

    Работает только с провайдером, заявившим поддержку зрения (Б.3, п.1: разделение
    ролей — изображение остаётся данными документа, а не системной инструкцией) —
    без ключа доступа к такому провайдеру эскалация не выполняется и не сбоит.
    """
    cfg = ctx.rules_config.get("llm_layer", {}).get("vision_escalation", {})
    if not cfg.get("enabled", False) or not getattr(ctx.provider, "supports_vision", False):
        return []
    max_images = int(cfg.get("max_images", 6))
    dpi = int(cfg.get("render_dpi", 150))
    min_text_facts = int(cfg.get("min_text_facts", 2))
    images: list[ImageBlock] = []
    for room in sorted(rooms):
        if len(images) >= max_images:
            break
        before_facts, after_facts = grouped_before.get(room, []), grouped_after.get(room, [])
        if not needs_escalation(before_facts, after_facts, min_text_facts):
            continue
        budget = max_images - len(images)
        images.extend(render_room_images(room, "до", before_facts, dpi, budget)[:budget])
        budget = max_images - len(images)
        images.extend(render_room_images(room, "после", after_facts, dpi, budget)[:budget])
    return images


async def run_llm_layer(before: DocumentSet | None, after: DocumentSet | None,
                        transition: str, ctx) -> list[Detection]:
    """Запустить семантический слой. Ошибка провайдера не роняет анализ."""
    if ctx.provider is None or not ctx.rules_config.get("llm_layer", {}).get("enabled", True):
        return []
    rooms = candidate_rooms(before, after)
    grouped_before = facts_by_room(before, rooms)
    grouped_after = facts_by_room(after, rooms)
    priority_before = _priority_ids(grouped_before)
    priority_after = _priority_ids(grouped_after)
    facts_before, blocks_before, index_before = _side_facts(before, "before", priority_before)
    facts_after, blocks_after, index_after = _side_facts(after, "after", priority_after)
    index = {**index_before, **index_after}
    if not blocks_before and not blocks_after:
        return []

    images = _vision_images(rooms, grouped_before, grouped_after, ctx)
    clauses = ctx.norms.search("отступление рабочей документации от проектных решений "
                               "армирование класс бетона", top_k=5)
    instruction = INSTRUCTION
    if images:
        instruction += (" К запросу приложены отрисованные листы для помещений, где текста "
                        "недостаточно для сравнения — прочти их так же, как и текст, и укажи "
                        "номер соответствующего факта о помещении в fact_ids находки.")
    req = CompletionRequest(
        task="semantic_diff",
        system=SYSTEM_RULES,
        facts=facts_before + facts_after,
        norm_clauses=[c.to_norm_ref() | {"text": c.text} for c in clauses],
        untrusted_blocks=blocks_before + blocks_after,
        images=images,
        prompt_version="2.3",
        context={"instruction": instruction, "transition": transition},
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
        except SchemaViolationError:
            continue
        except Exception:      # noqa: BLE001 — недоступность провайдера не роняет анализ
            return None
    return None


def _to_detections(parsed: LLMFindings, index: dict, ctx) -> list[Detection]:
    """Превратить вывод модели в находки, отбрасывая ссылки на несуществующие факты."""
    out: list[Detection] = []
    for item in parsed.findings:
        evidence = []
        for position, fact_id in enumerate(item.fact_ids):
            pair = index.get(fact_id)
            if pair is None:
                continue                      # модель сослалась на несуществующий факт
            fact, doc = pair
            # Первая ссылка — проектное требование, последующие — фактическое состояние:
            # без разделения ролей интерфейс не сможет показать «было» и «стало».
            role = "требование" if position == 0 else "факт"
            evidence.append(Evidence.from_fact(fact, doc, role))
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
