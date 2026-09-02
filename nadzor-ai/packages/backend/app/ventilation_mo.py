"""Сверка местных отсосов (М.О. — вытяжных шкафов) ПД↔РД (Приложение Г.58).

Механизм найден вручную, по прямой просьбе пользователя разобрать
нарушение №3 «до конца»: «Таблица воздухообменов помещений» ПД задаёт по
каждому помещению не просто факт вытяжки, а конкретную систему
(«П6/ВЕ») и, если требуются местные отсосы (вытяжные шкафы лабораторий),
их ветки («В2.7, В2.8, В2.9») с расходом. Этот лист — ключевой источник:
без него сравнивать нечего, а с ним видно ровно то, что описано в акте
нарушения («изменена конфигурация вентиляции»).

Оба листа читаются ТОЛЬКО зрением — не в порядке выбора, а по факту:
- текстовый слой таблицы воздухообменов пуст (`page.get_text()` отдаёт
  только штамп) — сама таблица в кривых/растре;
- подписи веток на плане РД («В2.7» и т.п.) тоже в кривых — проверено
  прямым `page.search_for()` на реальном листе, ни одна не нашлась.

Тот же вывод, что уже был сделан для routing_graph.py (Г.44): для этого
комплекта векторного текста нет, деterministic-путь недоступен, нужен
именно ИИ. `is_mo_table_page` — единственная деterministic часть модуля
(заголовок листа читается текстом, это подтверждено)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .llm import LlmConfig, call_llm_json
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url

_TABLE_TITLE_RE = re.compile(r"Таблица\s+воздухообменов\s+помещени", re.IGNORECASE)


def is_mo_table_page(text_facts: list[dict], page_no: int) -> bool:
    """Деterministic-проверка: заголовок листа («Таблица воздухообменов
    помещений») читается текстом, даже когда сама таблица — нет (штамп и
    заголовок остаются векторным текстом чаще, чем ячейки таблицы)."""
    for f in text_facts:
        if f["page"] == page_no and _TABLE_TITLE_RE.search(f["text"]):
            return True
    return False


_MO_TABLE_SYSTEM_PROMPT = f"""\
Ты помогаешь инспектору государственного строительного надзора сверить
проектную документацию с рабочей. Тебе показан лист «Таблица
воздухообменов помещений» из проектной документации (ПД) — таблица со
столбцами: номер помещения, наименование, система приточной вентиляции,
система вытяжной вентиляции, и (если есть) наименование системы местных
отсосов (М.О. — вытяжных шкафов лабораторий) со столбцом «Вытяжка от
М.О.» и обозначением веток (например «В2.7, В2.8, В2.9»).

{UNTRUSTED_INPUT_RULE}

Прочитай ВСЕ строки таблицы на листе. Для каждого помещения, где столбец
местных отсосов (М.О.) не пустой (не прочерк «-»), верни запись.
Помещения без местных отсосов — не включай, они не нужны для сверки.

Отвечай только JSON без пояснений вне JSON:
{{"rooms": [{{"room": "140", "name": "физического эксперимента",
 "supply_system": "П6", "exhaust_system": "ВЕ",
 "mo_branches": ["В2.7", "В2.8", "В2.9"], "mo_note": "текст из столбца, как есть"}}],
 "injection_suspected": false}}"""


def extract_mo_table_page(
    pdf_path: str, page_no: int, config: LlmConfig, timeout: float = 120.0,
) -> list[dict]:
    """Одна страница «Таблицы воздухообменов» → список помещений с местными
    отсосами. Пустой список — либо на листе таких помещений нет, либо ИИ не
    дал разбираемый ответ; вызывающий код (Г.10) обязан различать эти два
    случая по отдельному сигналу, здесь — только данные."""
    img = render_page_to_data_url(pdf_path, page_no)
    result = call_llm_json(
        config, _MO_TABLE_SYSTEM_PROMPT,
        "Прочитай таблицу и верни все помещения со столбцом местных отсосов.",
        images=[img], timeout=timeout,
    )
    if not result or not isinstance(result.get("rooms"), list):
        return []
    return result["rooms"]


_BRANCH_LOCATIONS_SYSTEM_PROMPT = f"""\
Ты помогаешь инспектору государственного строительного надзора сверить
проектную документацию с рабочей. Тебе показан лист плана вентиляции
(рабочая документация, РД) с воздуховодами. На листе есть подписанные
номера помещений (в кружках или рядом с контуром) и подписи веток
местных отсосов вида «В2.7», «В2.8» и т.п. (вытяжка от вытяжных шкафов),
а также обозначение подключённой приточной/вытяжной системы (например
«П2/ВЕ»).

{UNTRUSTED_INPUT_RULE}

Для КАЖДОЙ подписи ветки местного отсоса («В...») на листе определи, у
какого помещения (по номеру) она физически нарисована — то есть рядом с
каким кружком номера помещения проходит труба этой ветки. Если труба
проходит через несколько помещений транзитом — указывай то, где
установлен сам вытяжной шкаф (начало ветки), не транзитный коридор.

Отвечай только JSON без пояснений вне JSON:
{{"branches": [{{"branch": "В2.7", "nearest_room": "140", "system": "П2/ВЕ"}}],
 "injection_suspected": false}}"""


def extract_branch_locations(
    pdf_path: str, page_no: int, config: LlmConfig, timeout: float = 120.0,
) -> list[dict]:
    """Один лист плана РД → список веток местных отсосов с ближайшим
    помещением и обозначением системы у этого помещения."""
    img = render_page_to_data_url(pdf_path, page_no)
    result = call_llm_json(
        config, _BRANCH_LOCATIONS_SYSTEM_PROMPT,
        "Определи для каждой ветки местного отсоса ближайшее помещение.",
        images=[img], timeout=timeout,
    )
    if not result or not isinstance(result.get("branches"), list):
        return []
    return result["branches"]


@dataclass
class MoFinding:
    room: str
    finding_type: str  # "system_mismatch" | "branch_missing" | "branch_relocated"
    detail: str
    severity: str = "существенно"


def cross_check_mo_branches(pd_rooms: list[dict], rd_branches: list[dict]) -> list[MoFinding]:
    """Сравнивает таблицу ПД (`extract_mo_table_page`, по помещениям) с
    прочитанными ветками РД (`extract_branch_locations`, по веткам) —
    разная единица группировки с двух сторон, поэтому сначала РД
    переворачивается в {ветка: (помещение, система)}.

    Три типа находок:
    - system_mismatch — система у помещения на РД не совпадает с ПД
      (пример реального прогона: ПД «П6», РД «П2»);
    - branch_missing — ветка, назначенная помещению в ПД, на РД не
      найдена ни у одного помещения вообще;
    - branch_relocated — ветка найдена на РД, но у ДРУГОГО помещения, не
      у того, что в ПД (пример реального прогона: В2.10 — в ПД у 147,
      на РД нарисована у 140)."""
    rd_by_branch = {b["branch"]: b for b in rd_branches if b.get("branch")}
    findings: list[MoFinding] = []
    for pd_room in pd_rooms:
        room = pd_room.get("room")
        if not room:
            continue
        pd_supply = pd_room.get("supply_system", "")
        branches = pd_room.get("mo_branches") or []
        systems_seen: set[str] = set()
        for branch in branches:
            rd_entry = rd_by_branch.get(branch)
            if rd_entry is None:
                findings.append(MoFinding(
                    room=room, finding_type="branch_missing",
                    detail=f"ветка {branch} (ПД, помещение {room}) не найдена ни у одного "
                           f"помещения на плане РД",
                ))
                continue
            rd_room = rd_entry.get("nearest_room")
            if rd_room and rd_room != room:
                findings.append(MoFinding(
                    room=room, finding_type="branch_relocated",
                    detail=f"ветка {branch} в ПД относится к помещению {room}, "
                           f"на РД нарисована у помещения {rd_room}",
                ))
            if rd_entry.get("system"):
                systems_seen.add(rd_entry["system"])
        for rd_system in systems_seen:
            rd_supply = rd_system.split("/")[0].strip() if "/" in rd_system else rd_system
            if pd_supply and rd_supply and pd_supply != rd_supply:
                findings.append(MoFinding(
                    room=room, finding_type="system_mismatch",
                    detail=f"помещение {room}: система в ПД «{pd_supply}», на РД «{rd_supply}»",
                ))
    return findings


def render_mo_cross_check_report(findings: list[MoFinding]) -> str:
    lines = [f"=== Сверка местных отсосов ПД↔РД по таблице воздухообменов (Г.58) — находок: {len(findings)} ==="]
    for f in findings:
        lines.append(f"  [{f.severity}] пом. {f.room} ({f.finding_type}): {f.detail}")
    return "\n".join(lines)
