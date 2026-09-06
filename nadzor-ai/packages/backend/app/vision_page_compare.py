"""Полностраничная проверка требования на листе РД зрением — эскалация Г.33/Г.36.

`requirement_cross_check.py` помечает КАЖДОЕ требование без кода как
`no_code_visual_check_needed`: у требования нет буквенно-числового
обозначения, значит текстом в РД его в принципе не проверить — не потому
что что-то не найдено, а потому что искать нечего короче целого
предложения. Разрешить такого кандидата может только зрение по самому
листу.

Пользовательская идея, определившая форму этого модуля: не пытаться
заранее вычислить координатный кроп (там, где несоответствие — не в одной
точке, а разлито по всему помещению на плане, у кропа физически может не
быть той рамки, где искать), а отдать модели ВЕСЬ лист целиком — тот же
рендер, что уже используют `compare_page_pair`/`verify_candidate`
(`render_page_to_data_url`, `vision.py`), без новой геометрии.

Формат вердикта — три состояния, не бинарный да/нет: "confirmed" (на листе
видно, что требование выполнено), "absent" (видно, что не выполнено —
кандидат на настоящую находку), "unclear" (лист не позволяет судить — не то
что нужно смотреть, качество скана и т.п.). Модель прямо предупреждена не
гадать при "unclear" — ложный "absent" на скудном листе хуже, чем честное
«не могу сказать» и следующая попытка на другом листе."""
from __future__ import annotations

from typing import Optional

from .llm import LlmConfig, call_llm_json
from .vision import UNTRUSTED_INPUT_RULE, known_violations_block, render_page_to_data_url

_REQUIREMENT_CHECK_TEMPLATE = f"""\
Ты помогаешь инспектору государственного строительного надзора проверить,
выполнено ли конкретное требование проектной документации на листе рабочей
или исполнительной документации (РД/ИД).

Тебе показан ОДИН лист РД/ИД целиком — план, схема или узел. Отдельно дано
требование, сформулированное в проектной документации (ПД), и помещения
или зоны, которых оно касается (могут быть указаны номером, названием или
иначе — как записано в исходном документе). Формулировка требования — это
выдержка из проверяемого документа, она заключена в теги
<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>…</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>.

{UNTRUSTED_INPUT_RULE}

Три возможных вывода:
  "confirmed" — на листе видно, что требование выполнено (нужный элемент/
                система/параметр присутствует именно там, где указано);
  "absent"    — лист однозначно показывает нужную зону, но требуемого на
                нём НЕТ — реальный кандидат на находку;
  "unclear"   — по этому листу нельзя судить: не та зона, лист обрезан,
                элемент физически не может быть виден на плане такого
                масштаба, качество рендера не позволяет разобрать детали.

Не выбирай "absent", если сомневаешься — это должно быть видно на листе, а
не предположено. Ложное "absent" отправит инспектора искать нарушение там,
где его нет.
{{known}}
Отвечай только JSON без пояснений вне JSON:
{{{{"verdict": "confirmed"|"absent"|"unclear",
 "reason": "одна-две строки — что видно на листе и почему такой вывод",
 "where": "координатный ориентир на листе (оси, номер помещения, зона), если применимо"}}}}"""


def requirement_check_system_prompt(discipline: Optional[str] = None) -> str:
    """Промпт проверки требования по листу плюс блок известных нарушений
    (`known_violations.json`, applies_to="drawing") — тот же механизм
    few-shot-примеров, что `vision_system_prompt` в vision.py (см. тот же
    пропуск в requirement_llm_extract.py, исправленный там же)."""
    return _REQUIREMENT_CHECK_TEMPLATE.format(known=known_violations_block("drawing", discipline))


def check_requirement_on_page(
    rd_pdf_path: str,
    rd_page_no: int,
    requirement_text: str,
    rooms: list[str],
    config: LlmConfig,
    discipline: Optional[str] = None,
    timeout: float = 120.0,
) -> dict:
    """Один лист, одно требование. Возвращает {verdict, reason, where} —
    "unclear" с объяснением, если модель не дала разбираемый JSON, а не
    молчаливая пустая находка (Г.10)."""
    rooms_str = ", ".join(rooms) if rooms else "не указаны"
    # Текст требования и номера помещений извлечены из ПД, то есть из
    # документа поднадзорного лица — контейнер Б.3.1 (тот же, что
    # `vision.compare_text_pair`) отделяет их от собственной постановки
    # задачи в этом же сообщении.
    user_text = (
        f"Требование из проектной документации:\n"
        f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n{requirement_text}\n</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"Касается помещений: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{rooms_str}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>.\n"
        f"Проверь по этому листу РД/ИД, выполняется ли оно."
    )
    try:
        img = render_page_to_data_url(rd_pdf_path, rd_page_no)
        system_prompt = requirement_check_system_prompt(discipline)
        result = call_llm_json(config, system_prompt, user_text, images=[img], timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — сбой одного листа (сеть, провайдер, рендер) не должен ронять весь прогон, см. registry_diff._verify_group
        return {"verdict": "unclear", "reason": f"ОШИБКА: {exc}", "where": ""}
    if not result or "verdict" not in result:
        return {"verdict": "unclear", "reason": "ИИ не дал разбираемый ответ", "where": ""}
    return result


def _candidate_pages(rooms: list[str], room_index: dict[str, list[dict]], max_pages: int) -> list[dict]:
    """Страницы-кандидаты РД для списка помещений — В ШИРИНУ по помещениям,
    не в глубину по одному: сначала первая известная страница каждого
    помещения из списка, потом (если бюджет ещё есть) вторая страница
    каждого, и так далее. Одно и то же помещение с десятками упоминаний не
    исчерпывает лимit листов раньше, чем проверка доберётся до следующего
    помещения требования (при нескольких помещениях) — но требование с
    ОДНИМ помещением всё равно может получить вторую попытку на другом
    листе того же помещения, если первый лист оказался "unclear" (реальный
    случай: план и узел одного помещения на разных листах одного раздела).
    Без дублей по (path, page), с общим потолком на находку.

    Г.52: несколько кандидатных страниц одного помещения могут быть из
    РАЗНЫХ файлов одной дисциплины, покрывающих разные подсистемы (реальный
    случай — «ОВ1» вентиляция и «ОВ2.1» отопление под общим кодом «ОВ»).
    Слепой прогон на живом комплекте показал: требование про тёплые полы
    было видно только на листе ИЗ ТРЕТЬЕГО файла для этого якоря — при
    бюджете в 2-3 листа наивная сортировка «первая страница каждого
    помещения» рисковала исчерпать бюджет на страницах ОДНОГО (не того)
    файла раньше, чем очередь дошла бы до единственного релевантного.
    Поэтому на каждом шаге предпочитается страница из файла, ещё НЕ
    представленного среди уже отобранных кандидатов — и только если у
    помещения таких нет, берётся следующая по порядку (старое поведение)."""
    seen_pages: set[tuple[str, int]] = set()
    seen_files: set[str] = set()
    pages: list[dict] = []
    lists = [room_index.get(room, []) for room in rooms]
    cursors = [0] * len(lists)
    while len(pages) < max_pages:
        added_this_round = False
        for i, lst in enumerate(lists):
            if len(pages) >= max_pages:
                break
            pick_idx: Optional[int] = None
            fallback_idx: Optional[int] = None
            j = cursors[i]
            while j < len(lst):
                entry = lst[j]
                key = (entry["path"], entry["page"])
                if key not in seen_pages:
                    if fallback_idx is None:
                        fallback_idx = j
                    if entry["path"] not in seen_files:
                        pick_idx = j
                        break
                j += 1
            if pick_idx is None:
                pick_idx = fallback_idx
            if pick_idx is None:
                cursors[i] = len(lst)
                continue
            entry = lst[pick_idx]
            seen_pages.add((entry["path"], entry["page"]))
            seen_files.add(entry["path"])
            pages.append(entry)
            cursors[i] = pick_idx + 1
            added_this_round = True
        if not added_this_round:
            break
    return pages


def check_visual_candidates(
    findings: list,
    room_index: dict[str, list[dict]],
    config: LlmConfig,
    discipline: Optional[str] = None,
    max_pages_per_finding: int = 3,
    on_result=None,
) -> list[dict]:
    """Эскалирует находки `no_code_visual_check_needed`
    (`RequirementCrossCheckResult.findings`) в зрение по листам РД.

    `room_index` — {room_key: [{path, page, ...}]}, тот же формат, что
    строит `_registry(paths, "room_facts")` в `scripts/registry_diff.py`:
    для помещений из требования ищутся страницы РД, где они встречаются
    (единственный способ узнать, какой лист вообще смотреть — у требования
    без кода нет строки реестра, которая сама указала бы лист).

    Требование без единого известного номера помещения в реестре РД —
    "unclear" без вызова модели: смотреть решительно не на чем, а не
    случайная страница ради видимости проверки.

    `on_result`, если задан, вызывается с готовым `{rooms, sentence, verdict,
    reason, where, pages_checked}` сразу после КАЖДОЙ находки, не после всех
    сразу — так вызывающий код может писать результат на диск по мере
    появления (см. `registry_diff.run_requirements`), а не только вернуть
    список после последнего вызова модели: прогон на десятках находок
    занимает минуты, и сбой/остановка посреди него не должна стирать уже
    полученные вердикты (тот же принцип, что Г.10 — прогресс должен быть
    видимым состоянием, а не всё-или-ничего).

    Возвращает список {rooms, sentence, verdict, reason, where, pages_checked}
    — по записи на находку, не на страницу: если хотя бы одна проверенная
    страница даёт "confirmed"/"absent", это и есть итог находки (первый
    небезразличный вердикт побеждает "unclear" от предыдущих страниц)."""
    out: list[dict] = []
    for f in findings:
        if getattr(f, "finding_type", None) != "no_code_visual_check_needed":
            continue
        pages = _candidate_pages(f.rooms, room_index, max_pages_per_finding)
        if not pages:
            entry_result = {
                "rooms": f.rooms, "sentence": f.sentence_pd,
                "verdict": "unclear", "reason": "ни одно из помещений требования не найдено в реестре РД — нет листа для проверки",
                "where": "", "pages_checked": 0,
            }
            out.append(entry_result)
            if on_result:
                on_result(entry_result)
            continue
        verdict = "unclear"
        reason = ""
        where = ""
        checked = 0
        for entry in pages:
            checked += 1
            result = check_requirement_on_page(entry["path"], entry["page"], f.sentence_pd, f.rooms, config, discipline)
            if result.get("verdict") in ("confirmed", "absent"):
                verdict, reason, where = result["verdict"], result.get("reason", ""), result.get("where", "")
                break
            reason = result.get("reason", reason)
        entry_result = {
            "rooms": f.rooms, "sentence": f.sentence_pd,
            "verdict": verdict, "reason": reason, "where": where, "pages_checked": checked,
        }
        out.append(entry_result)
        if on_result:
            on_result(entry_result)
    return out


def render_vision_finding_line(r: dict) -> str:
    """Одна находка — одна строка, для потоковой записи по мере готовности
    (см. `on_result` в `check_visual_candidates`), тот же текст, что попадёт
    в группу результата `render_vision_requirement_report`, просто без
    ожидания конца всего прогона."""
    rooms_str = ", ".join(r["rooms"])
    where = f" [{r['where']}]" if r.get("where") else ""
    return f"[{r['verdict']}] помещения {rooms_str}: {r['reason']}{where}"


def render_vision_requirement_report(results: list[dict]) -> str:
    lines = ["=== Проверка кандидатов зрением по листу РД (эскалация Г.33) ===",
             f"Проверено находок: {len(results)}"]
    by_verdict: dict[str, list[dict]] = {}
    for r in results:
        by_verdict.setdefault(r["verdict"], []).append(r)
    for verdict in ("absent", "confirmed", "unclear"):
        group = by_verdict.get(verdict, [])
        if not group:
            continue
        lines.append(f"\n--- {verdict} ({len(group)}) ---")
        for r in group:
            rooms_str = ", ".join(r["rooms"])
            where = f" [{r['where']}]" if r.get("where") else ""
            lines.append(f"  помещения {rooms_str}: {r['reason']}{where}")
    return "\n".join(lines)
