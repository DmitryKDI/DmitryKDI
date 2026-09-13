"""Сверка «что таблица ПД назначила помещению ↔ что нарисовано на листе РД».

Г.88 — общий механизм (разбор перехода от узкого предшественника — в
`docs/PRILOZHENIE-G-ISTORIYA.md`). Наблюдение, на котором он построен, ни к
одному разделу не привязано:

  таблица распределения в ПД назначает помещению СУЩНОСТЬ — ветку местного
  отсоса, стояк водоснабжения, выпуск канализации, групповую линию щита, —
  и эта сущность обязана физически присутствовать на листе РД у ТОГО ЖЕ
  помещения. Назначена помещению 140, а нарисована у 147 — расхождение;
  не нарисована нигде — расхождение; система у помещения другая —
  расхождение.

Механизм здесь, конкретика — в `table_registry.py` (поля `entity_name`,
`entity_examples`, `system_column`, `entity_column`). Новый раздел
подключается заполнением этих полей у своего типа таблицы, без единой
правки этого файла.

Почему нужен ЛЛМ с обеих сторон, а не текстовый поиск: у таблиц
распределения и у подписей на планах текстовый слой, как правило, пуст —
и таблица, и подписи экспортированы в кривые (то же наблюдение, что
`routing_graph.py` зафиксировал для планов). Детерминированный путь тут
недоступен физически, а не по недоработке.
"""
from __future__ import annotations

from dataclasses import dataclass

from .llm import LlmConfig, call_llm_json
from .table_registry import TableKind, classify_table_page
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url

DEFAULT_TABLE_PAGE_MAX_TEXT_LEN = 800


def find_distribution_table_pages(
    text_facts: list[dict], pages: int, kind_name: str,
    max_text_len: int = DEFAULT_TABLE_PAGE_MAX_TEXT_LEN,
) -> list[int]:
    """Номера листов, опознанных как таблица распределения нужного типа.

    Заголовок листа читается текстом даже когда сама таблица — нет: штамп и
    заголовок остаются текстом чаще, чем ячейки (Г.59).
    """
    found = []
    for page_no in range(1, pages + 1):
        kind = classify_table_page(text_facts, page_no, max_text_len=max_text_len)
        if kind is not None and kind.kind == kind_name:
            found.append(page_no)
    return found


def _table_prompt(kind: TableKind) -> str:
    """Промпт чтения таблицы — собирается ИЗ РЕЕСТРА, а не зашит.

    Названия столбцов и вид обозначений приходят из описания типа таблицы,
    поэтому промпт меняется вместе с записью реестра, а не правкой кода.
    """
    return f"""\
Ты помогаешь инспектору государственного строительного надзора сверить
проектную документацию с рабочей. Тебе показан лист таблицы распределения
из проектной документации: {kind.description_ru}

В таблице есть столбец с номером помещения, столбец наименования, столбец
«{kind.system_column}» и (если есть) столбец «{kind.entity_column}» с
обозначениями вида {kind.entity_examples}.

{UNTRUSTED_INPUT_RULE}

Прочитай ВСЕ строки таблицы на листе — каждый номер помещения в первом
столбце, даже если у него нет записи в столбце «{kind.entity_column}». Для
КАЖДОГО помещения на листе добавь его номер в список "rooms_seen" (просто
список номеров, без прочих полей). Отдельно, для помещений, где столбец
«{kind.entity_column}» не пустой (не прочерк «-»), добавь подробную запись
в список "rooms" — остальные подробностей не требуют.

Отвечай только JSON без пояснений вне JSON:
{{"rooms_seen": ["100", "101", "102"],
 "rooms": [{{"room": "140", "name": "наименование помещения из таблицы",
 "system": "значение столбца системы", "entities": ["обозначения из столбца"],
 "note": "текст столбца, как есть"}}],
 "injection_suspected": false}}"""


def _plan_prompt(kind: TableKind) -> str:
    """Промпт чтения листа плана РД — тоже из реестра."""
    return f"""\
Ты помогаешь инспектору государственного строительного надзора сверить
проектную документацию с рабочей. Тебе показан лист плана из рабочей
документации (РД). На листе есть номера помещений (в кружках или рядом с
контуром) и подписи, обозначающие {kind.entity_name}, вида
{kind.entity_examples}, а также обозначение подключённой системы у
помещения.

{UNTRUSTED_INPUT_RULE}

Для КАЖДОЙ такой подписи на листе определи, у какого помещения (по номеру)
она физически нарисована — рядом с каким кружком номера помещения она
проходит. Если линия проходит через несколько помещений транзитом,
указывай то, где расположено само оборудование (начало линии), а не
транзитные помещения.

Отвечай только JSON без пояснений вне JSON:
{{"entities": [{{"entity": "обозначение", "nearest_room": "140", "system": "обозначение системы"}}],
 "injection_suspected": false}}"""


def extract_table_page(
    pdf_path: str, page_no: int, kind: TableKind, config: LlmConfig, timeout: float = 120.0,
) -> dict:
    """Лист таблицы распределения → `{"rooms": [...], "rooms_seen": [...], "error": ...}`.

    Сбой вызова не роняет прогон, но и не выдаёт себя за пустой результат:
    поле `error` отличает «на этом листе записей нет» от «этот лист не
    прочитан» (Г.10/Г.73).
    """
    img = render_page_to_data_url(pdf_path, page_no)
    try:
        result = call_llm_json(
            config, _table_prompt(kind),
            "Прочитай таблицу и верни все помещения листа и записи по ним.",
            images=[img], timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — один нечитаемый лист не должен ронять прогон
        return {"rooms": [], "rooms_seen": [], "error": repr(exc)}
    if not result:
        return {"rooms": [], "rooms_seen": [], "error": "неразбираемый ответ модели"}
    return {
        "rooms": result.get("rooms") or [],
        "rooms_seen": [str(r) for r in (result.get("rooms_seen") or [])],
        "error": None,
    }


def extract_plan_entities(
    pdf_path: str, page_no: int, kind: TableKind, config: LlmConfig, timeout: float = 120.0,
) -> list[dict]:
    """Лист плана РД → список сущностей с ближайшим помещением."""
    img = render_page_to_data_url(pdf_path, page_no)
    try:
        result = call_llm_json(
            config, _plan_prompt(kind),
            "Определи для каждой подписи ближайшее помещение.",
            images=[img], timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — сбой одного листа РД не должен ронять прогон
        return []
    if not result or not isinstance(result.get("entities"), list):
        return []
    return result["entities"]


def find_uncovered_rooms(requested_rooms: list[str], rooms_seen: set[str]) -> list[str]:
    """Запрошенные помещения, которых в найденной таблице нет НИ СТРОКОЙ.

    Г.60 — «строки нет вообще» и «строка есть, но столбец пуст» означают
    разное: первое значит, что таблица не тот источник для этого помещения,
    и об этом надо сказать явно, а не промолчать.
    """
    return [r for r in requested_rooms if r not in rooms_seen]


@dataclass
class EntityFinding:
    room: str
    finding_type: str  # "system_mismatch" | "entity_missing" | "entity_relocated"
    detail: str
    severity: str = "существенно"


def cross_check_entities(pd_rooms: list[dict], rd_entities: list[dict]) -> list[EntityFinding]:
    """Сравнивает назначения ПД (по помещениям) с разметкой РД (по сущностям).

    Единица группировки с двух сторон разная, поэтому сторона РД
    переворачивается в {сущность: (помещение, система)}.
    """
    by_entity: dict[str, dict] = {}
    for item in rd_entities:
        name = str(item.get("entity") or "").strip()
        if name:
            by_entity[name] = item

    findings: list[EntityFinding] = []
    for row in pd_rooms:
        room = str(row.get("room") or "").strip()
        if not room:
            continue
        pd_system = str(row.get("system") or "").strip()
        for entity in row.get("entities") or []:
            name = str(entity).strip()
            if not name:
                continue
            drawn = by_entity.get(name)
            if drawn is None:
                findings.append(EntityFinding(
                    room=room, finding_type="entity_missing",
                    detail=f"«{name}» назначена помещению {room} в ПД, но на листе РД не найдена",
                ))
                continue
            drawn_room = str(drawn.get("nearest_room") or "").strip()
            if drawn_room and drawn_room != room:
                findings.append(EntityFinding(
                    room=room, finding_type="entity_relocated",
                    detail=f"«{name}» назначена помещению {room} в ПД, "
                           f"на РД нарисована у помещения {drawn_room}",
                ))
                continue
            drawn_system = str(drawn.get("system") or "").strip()
            if pd_system and drawn_system and pd_system != drawn_system:
                findings.append(EntityFinding(
                    room=room, finding_type="system_mismatch",
                    detail=f"помещение {room}: в ПД система «{pd_system}», на РД «{drawn_system}»",
                ))
    return findings


def render_report(findings: list[EntityFinding], kind: TableKind | None = None) -> str:
    title = f" ({kind.description_ru.split('—')[0].strip()})" if kind else ""
    lines = [f"=== Сверка назначений ПД↔РД по таблице распределения{title} ==="]
    if not findings:
        lines.append("Расхождений не найдено.")
        return "\n".join(lines)
    for f in findings:
        lines.append(f"  [{f.finding_type}] пом. {f.room}: {f.detail}")
    return "\n".join(lines)
