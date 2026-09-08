"""Сверка «выполнено ли в РД то, что требует ПД» (Г.96).

Указание пользователя вместе с его предупреждением: «имей в виду, что может
быть много ложных срабатываний, ведь в РД меньше текста. Тут мы будем
смотреть по списку требований ПД и то, что в РД написано. Например, если в
ПД указано, что в таких-то местах делаем так, то система уже в РД смотрит
по чертежам (если нет требования в тексте), так ли сделано».

## Главное правило модуля

**Отсутствие подтверждения НИКОГДА не называется нарушением.** В рабочей
документации связного текста почти нет по природе: подписи чертежей
переведены в кривые (Г.8, измерено в Г.44/Г.59), а требование, которое в ПД
написано словами, в РД выражено линией на плане и позицией в спецификации.
Поэтому «не нашли в тексте РД» — ожидаемое состояние, а не улика. Инспектор
получает три статуса, и ни один из них не вердикт:

  подтверждено      — нашли прямое совпадение (текстом или на листе);
  требует проверки  — не подтвердили; названы листы, куда смотреть;
  не проверялось    — проверить было нечем (нет ключа, сбой, не за что
                      зацепиться визуально).

Это прямое следствие Б.6: система формирует гипотезы, а не заключения.

## Лестница проверки — от дешёвого к дорогому

1. **Токен в тексте РД.** Нормативный номер, марка или класс из требования
   ищется прямо в тексте (`requirement_cross_check._extract_token`, Г.48).
   Нашлось — вызова модели не нужно вовсе.
2. **Смысловая сверка текста РД моделью** (`requirement_text_verify`, Г.49):
   пачками, вызов на пачку со списком ещё нерешённых требований, а не на
   каждое требование отдельно.
3. **Зрение по листам РД** (`vision_page_compare`, Г.35) — только для
   требований, привязанных к номерам помещений: иначе не за что зацепиться,
   и лист выбирать не по чему. Требование к объекту целиком честно
   помечается «визуально проверять не по чему», а не гоняется вслепую.

Каждая следующая ступень работает ТОЛЬКО над тем, что не решила
предыдущая.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from .llm import LlmConfig
from .requirement_cross_check import _extract_token, _token_present_in
from .requirement_registry import Requirement

STATUS_CONFIRMED = "подтверждено"
STATUS_NEEDS_CHECK = "требует проверки"
STATUS_NOT_CHECKED = "не проверялось"

# Сколько листов РД смотреть зрением на одно требование. Держится малым
# намеренно: зрение — самая дорогая ступень (~6 с на лист, Г.30), а цель
# здесь не доказать выполнение, а показать инспектору, куда смотреть.
DEFAULT_MAX_VISUAL_PAGES = 3


@dataclass
class ComplianceItem:
    requirement: Requirement
    status: str
    detail: str
    evidence: str = ""
    # Листы РД, на которых стоит посмотреть глазами: [(имя файла, страница)]
    pages_to_check: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class ComplianceResult:
    items: list[ComplianceItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # Что в этом прогоне не выполнялось и почему — видимое состояние (Г.10).
    not_run: list[str] = field(default_factory=list)


def check_compliance(
    requirements: list[Requirement],
    rd_text_facts: list[dict],
    rd_sources: list[tuple[str, str]],
    config: LlmConfig | None,
    llm_verify: Callable | None = None,
    vision_check: Callable | None = None,
    candidate_pages: Callable | None = None,
    max_visual_pages: int = DEFAULT_MAX_VISUAL_PAGES,
) -> ComplianceResult:
    """Прогоняет список требований ПД по лестнице проверок против РД.

    `llm_verify`, `vision_check`, `candidate_pages` внедряются параметрами,
    а не берутся из модуля: так лестницу можно проверить тестом без сети и
    без реальных PDF, и так же видно, что модуль ничего не вызывает сам,
    когда ключа нет.
    """
    result = ComplianceResult()
    rd_text = "\n".join(f.get("text", "") for f in rd_text_facts)

    # --- Ступень 1: токен в тексте РД (без модели) ---
    pending: list[Requirement] = []
    for req in requirements:
        token = _extract_token(req.sentence)
        if token and _token_present_in(token, rd_text):
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_CONFIRMED,
                detail=f"обозначение «{token}» найдено в тексте рабочей документации",
                evidence=token,
            ))
        else:
            pending.append(req)

    if not pending:
        return _finish(result)

    if config is None:
        for req in pending:
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NOT_CHECKED,
                detail="ключ ИИ не задан — доступна только сверка по обозначению; "
                       "смысловая сверка текста и просмотр листов не выполнялись",
            ))
        result.not_run.append("смысловая сверка текста РД и просмотр листов — нет ключа ИИ")
        return _finish(result)

    # --- Ступень 2: смысловая сверка текста РД моделью ---
    verdicts: dict[str, dict] = {}
    if llm_verify is None:
        # Г.107 — ключ есть, а смысловая сверка не подключена: шаг просто не
        # выполнялся. Без этой строки прогон выглядел бы так, будто модель
        # текст РД прочитала и ничего не подтвердила (Г.10/Г.77).
        result.not_run.append(
            "смысловая сверка текста РД — шаг не подключён в этом прогоне")
    if llm_verify is not None:
        try:
            for v in llm_verify(pending, rd_text_facts, config):
                verdicts[str(v.get("sentence", ""))] = v
        except Exception as exc:  # noqa: BLE001 — сбой не должен стать «в РД нет»
            for req in pending:
                result.items.append(ComplianceItem(
                    requirement=req, status=STATUS_NOT_CHECKED,
                    detail=f"смысловая сверка текста не выполнена: {type(exc).__name__}: {exc}",
                ))
            result.not_run.append(f"смысловая сверка текста РД: {type(exc).__name__}: {exc}")
            return _finish(result)

    still_pending: list[Requirement] = []
    for req in pending:
        v = verdicts.get(req.sentence)
        if v is not None and v.get("verdict") == "confirmed":
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_CONFIRMED,
                detail=f"подтверждено по тексту РД: {v.get('reason', '')}".strip(),
                evidence=str(v.get("reason", "")),
            ))
        else:
            still_pending.append(req)

    # --- Ступень 3: зрение по листам РД ---
    for req in still_pending:
        pages: list[tuple[str, int]] = []
        # Г.106 — якорем может быть и подсказка по названию помещения, если
        # номер в тексте требования не назван. Она честно помечается: номер в
        # скобках это факт документа, совпадение по названию — догадка, и
        # инспектор должен видеть, по чему ему подобрали лист.
        anchor_rooms = req.rooms or req.rooms_by_name
        by_name = not req.rooms and bool(req.rooms_by_name)
        anchor_note = (" (помещения подсказаны по названию, номер в требовании не назван)"
                       if by_name else "")
        if anchor_rooms and candidate_pages is not None:
            pages = list(candidate_pages(anchor_rooms, rd_sources))[:max_visual_pages]

        if pages and vision_check is None:
            # Г.106 — листы подобраны, но смотреть их нечем. Раньше этого
            # состояния не существовало: подбор был завязан на наличие
            # зрения, и прогон без него докладывал «подходящих листов РД не
            # найдено» — то есть выдавал НЕВЫПОЛНЕННЫЙ шаг за отрицательный
            # результат поиска. Ровно та подмена, которую запрещает Г.10, и
            # заодно потеря главного, что нужно инспектору: куда смотреть.
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NEEDS_CHECK,
                detail="в тексте РД не подтверждено; листы для просмотра подобраны, "
                       "но просмотр изображения не выполнялся" + anchor_note,
                pages_to_check=pages))
            continue

        if not pages:
            # Требование к объекту целиком: лист выбирать не по чему. Гонять
            # зрение вслепую по всему тому дороже и бесполезнее, чем сказать
            # инспектору прямо, что здесь нужен его глаз.
            reason = ("в тексте РД не подтверждено; требование не привязано к номерам "
                      "помещений и по названию их подобрать не удалось, поэтому лист "
                      "для просмотра выбрать не по чему"
                      if not anchor_rooms else
                      "в тексте РД не подтверждено; подходящих листов РД не найдено"
                      + anchor_note)
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NEEDS_CHECK, detail=reason))
            continue

        seen: dict | None = None
        for pdf_path, page_no in pages:
            try:
                # Г.106 — модели передаётся `req.rooms`, а НЕ `anchor_rooms`:
                # подсказка по названию выбирает, какой лист открыть, но сама
                # в вопрос к модели не входит. Иначе догадка «похоже, речь об
                # этих помещениях» пришла бы к модели утверждением, и её
                # ответ подтверждал бы не требование ПД, а нашу же гипотезу.
                # Тест: test_vision_is_not_told_the_guessed_rooms.
                seen = vision_check(pdf_path, page_no, req.sentence, req.rooms, config)
            except Exception as exc:  # noqa: BLE001 — один лист не роняет прогон
                seen = {"verdict": "unclear", "reason": f"{type(exc).__name__}: {exc}"}
            if seen.get("verdict") == "confirmed":
                result.items.append(ComplianceItem(
                    requirement=req, status=STATUS_CONFIRMED,
                    detail=f"подтверждено на листе: {seen.get('reason', '')}".strip(),
                    evidence=str(seen.get("where", "")),
                    pages_to_check=pages,
                ))
                break
        else:
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NEEDS_CHECK,
                detail="на просмотренных листах подтверждения нет — посмотреть глазами",
                pages_to_check=pages,
            ))
    return _finish(result)


def _finish(result: ComplianceResult) -> ComplianceResult:
    result.counts = dict(Counter(i.status for i in result.items))
    return result


def render_compliance_report(result: ComplianceResult) -> str:
    """Отчёт для инспектора.

    Обязан начинаться с оговорки о статусе выводов: это гипотезы для
    проверки на объекте, а не заключение о нарушении (Б.6). Без неё
    «требует проверки» на 60 позициях читается как 60 нарушений, чего в
    данных нет и быть не может — в РД мало текста по природе.
    """
    lines = [
        "=== Соответствие рабочей документации требованиям проектной ===",
        "Это НЕ ЯВЛЯЕТСЯ ЗАКЛЮЧЕНИЕМ о нарушениях. «Требует проверки» означает,",
        "что подтверждения не нашлось в доступных данных: в рабочей документации",
        "связного текста мало по природе (подписи чертежей переведены в кривые),",
        "и решение принимает инспектор, посмотрев названные листы.",
        "",
    ]
    for status in (STATUS_CONFIRMED, STATUS_NEEDS_CHECK, STATUS_NOT_CHECKED):
        lines.append(f"{status}: {result.counts.get(status, 0)}")
    if result.not_run:
        lines.append("")
        lines.append("Не выполнялось в этом прогоне:")
        lines.extend(f"  - {x}" for x in result.not_run)

    for status in (STATUS_NEEDS_CHECK, STATUS_NOT_CHECKED, STATUS_CONFIRMED):
        group = [i for i in result.items if i.status == status]
        if not group:
            continue
        lines.append("")
        lines.append(f"--- {status} ({len(group)}) ---")
        for item in group:
            req = item.requirement
            head = f"  [{req.section or '?'} стр.{req.page}] {req.summary or req.sentence}"
            lines.append(head)
            lines.append(f"      {item.detail}")
            if item.pages_to_check:
                where = ", ".join(f"{name}: лист {p}" for name, p in item.pages_to_check)
                lines.append(f"      смотреть: {where}")
    return "\n".join(lines)
