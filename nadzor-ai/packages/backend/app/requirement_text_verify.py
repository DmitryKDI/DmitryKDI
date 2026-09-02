"""Семантическая сверка общих требований (форма 3, Г.47) с прозой РД —
Приложение Г.49.

`requirement_cross_check.cross_check_general_requirements` (Г.48) сверяет
форму 3 ТОЛЬКО подстрокой — токен (ГОСТ/марка/фраза класса) либо
буквально есть в тексте РД, либо нет. Пользователь прямо указал: так и
должно было не найтись — «текст [в РД] отличается», это не брак токена, а
предел ЛЮБОГО текстового поиска подстрокой. Нужен шаг, который читает
текст РД ПО СМЫСЛУ, а не ищет совпадение символов: то же самое действие
может быть в РД описано другими словами, другим порядком, с другим
числом деталей — человек это видит сразу, простой grep — никогда.

Отличие от `vision_page_compare.py` (эскалация формы 2 в зрение): та
работает ПОЛИСТНО — показывает модели один лист-картинку целиком, нужен
якорь (номер помещения), чтобы выбрать, какой лист смотреть. У формы 3
такого якоря почти никогда нет (см. Г.47/Г.48 — только 2 из 84 реальных
требований несут номер помещения), поэтому эскалация в зрение форме 3 не
подходит вообще: нечем выбрать один лист из сотен. Здесь вместо этого —
ТЕКСТОВЫЙ корпус РД целиком, пачками под потолок символов на вызов (та же
разбивка, что `requirement_llm_extract.py`), и модель читает каждую пачку
в поисках любого из ещё нерешённых требований — так один требуемый факт
может обнаружиться в любом месте корпуса, без предположений о том, где
искать.

Один вызов — ОДНА пачка текста РД против ВСЕХ ещё нерешённых требований
сразу (не одно требование на вызов): 84 требования × десятки пачек одним
собственным вызовом на пару — сотни вызовов, неприемлемая стоимость;
пачка текста, а модель сама решает, к каким из показанных требований она
вообще относится — тот же принцип экономии, что уже применяет
`requirement_llm_extract.py` для извлечения (одна пачка — много находок
за вызов, не находка на вызов).

Три состояния вердикта, тот же принцип «не гадать», что и везде в этом
пакете (Г.10): "confirmed" — фрагмент РД явно показывает, что требуемое
выполнено; "absent" — фрагмент явно ПРО ЭТОТ вопрос, но показывает, что
не выполнено; фрагмент, который требования не касается вообще, — просто
не упоминается в ответе (не "unclear" на каждую пачку каждый раз, это
не пропуск, а сознательная экономия — модель прямо просят отвечать
только там, где есть основание судить). Требование, не получившее ни
одного `confirmed`/`absent` ни на одной из пачек, остаётся `unclear` —
честно «текст РД не даёт основания», не молчаливое исчезновение из
отчёта."""
from __future__ import annotations

from typing import Optional

from .llm import LlmConfig, call_llm_json
from .requirement_registry import Requirement
from .vision import UNTRUSTED_INPUT_RULE, known_violations_block

_TEXT_VERIFY_TEMPLATE = f"""\
Ты помогаешь инспектору государственного строительного надзора проверить,
отражено ли в тексте рабочей документации (РД) требование из проектной
документации (ПД) — ПО СМЫСЛУ, а не по совпадению отдельного слова или
короткого обозначения (это уже проверено раньше отдельным, более узким
способом). Одно и то же действие/элемент/параметр в РД может быть описано
другими словами, другим порядком, с другим набором деталей — читай так,
как читал бы инспектор, а не ищи повтор фразы.

{UNTRUSTED_INPUT_RULE}

Тебе показан ОДИН фрагмент текста РД (это может быть только часть тома,
дальше пойдут другие фрагменты того же документа) и пронумерованный
список ещё не решённых требований ПД.

Для КАЖДОГО требования, по которому именно ЭТОТ фрагмент даёт достаточно
оснований для вывода, — верни вердикт:
  "confirmed" — фрагмент РД показывает, что требуемое действительно
                сделано (даже если сформулировано другими словами);
  "absent"    — фрагмент РД явно касается именно этого вопроса, но
                показывает, что требуемое НЕ сделано или сделано иначе.

Если фрагмент вообще не затрагивает требование — НЕ включай его в ответ.
Не пытайся дать вердикт по каждому требованию на каждом фрагменте — так
теряется смысл постраничной проверки; отвечай только там, где текст
фрагмента прямо даёт основание судить.
{{known}}
Список ещё не решённых требований ПД дан в отдельном сообщении,
пронумерован «R1», «R2» и т.д. — используй эти номера в ответе.

Отвечай только JSON без пояснений вне JSON:
{{{{"verdicts": [
  {{{{"id": "R<номер требования из списка>",
   "verdict": "confirmed"|"absent",
   "reason": "одна-две строки — что именно в тексте РД даёт такой вывод"}}}}
]}}}}
Если этот фрагмент не даёт оснований ни по одному требованию — верни
{{{{"verdicts": []}}}}."""


def text_verify_system_prompt(discipline: Optional[str] = None) -> str:
    """Промпт семантической сверки плюс блок известных нарушений
    (`known_violations.json`, applies_to="text") — тот же механизм, что
    уже используют остальные ЛЛМ-промпты пакета."""
    return _TEXT_VERIFY_TEMPLATE.format(known=known_violations_block("text", discipline))


def _chunk_text_facts(text_facts: list[dict], max_chars: int) -> list[list[dict]]:
    """Та же разбивка на пачки под потолок символов, что
    `requirement_llm_extract.py` — независимая копия, не импорт: разные
    модули, разная зона ответственности (извлечение из ПД против сверки
    с РД), совпадение реализации крошечной утилиты не повод их связывать."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0
    for fact in text_facts:
        fact_len = len(fact["text"])
        if current and current_len + fact_len > max_chars:
            chunks.append(current)
            current, current_len = [], 0
        current.append(fact)
        current_len += fact_len
    if current:
        chunks.append(current)
    return chunks


def _render_chunk(chunk: list[dict]) -> str:
    return "\n\n".join(f"--- Страница {fact['page']} ---\n{fact['text']}" for fact in chunk)


def _render_requirements_block(pending: list[tuple[int, Requirement]]) -> str:
    return "\n".join(f"R{idx}: {req.sentence}" for idx, req in pending)


def verify_general_requirements_llm(
    general_requirements: list[Requirement],
    rd_text_facts: list[dict],
    config: LlmConfig,
    discipline: Optional[str] = None,
    max_chars_per_call: int = 6000,
    timeout: float = 120.0,
    on_result=None,
) -> list[dict]:
    """Сверка формы 3 с прозой РД по смыслу, не подстрокой (Г.49).

    Проходит корпус РД пачками; на каждой пачке модель получает список
    ЕЩЁ НЕРЕШЁННЫХ требований и текст пачки одним вызовом — не вызов на
    требование (см. докстринг модуля про стоимость). Требование выходит
    из списка «нерешённых», как только получило `confirmed`/`absent` на
    какой-то пачке — первый небезразличный вердикт побеждает, дальнейшие
    пачки его уже не проверяют (та же экономия, что
    `vision_page_compare.check_visual_candidates`).

    `on_result`, если задан, вызывается с готовым
    `{sentence, page, verdict, reason, chunks_checked}` сразу после того,
    как требование решилось (или после последней пачки, если осталось
    `unclear`) — тот же принцип потоковой записи по мере готовности, что
    `check_visual_candidates`/`registry_diff.run_requirements` (Г.10:
    прогон на десятках требований и пачек занимает время, сбой посреди
    него не должен стирать уже полученные вердикты).

    Возвращает по записи на требование: `{sentence, page, verdict, reason,
    chunks_checked}` — `verdict` "unclear", если ни одна пачка не дала
    оснований (честно, не пропуск из отчёта)."""
    system_prompt = text_verify_system_prompt(discipline)
    chunks = _chunk_text_facts(rd_text_facts, max_chars_per_call)

    pending: dict[int, Requirement] = {i: req for i, req in enumerate(general_requirements, 1)}
    resolved: dict[int, dict] = {}
    checked_count: dict[int, int] = {i: 0 for i in pending}

    for chunk in chunks:
        if not pending:
            break
        for idx in pending:
            checked_count[idx] += 1
        requirements_block = _render_requirements_block(sorted(pending.items()))
        user_text = (
            f"Список ещё не решённых требований ПД:\n{requirements_block}\n\n"
            f"Фрагмент текста РД:\n{_render_chunk(chunk)}"
        )
        try:
            result = call_llm_json(config, system_prompt, user_text, timeout=timeout)
        except Exception:  # noqa: BLE001 — сбой одной пачки не должен ронять сверку по остальным
            continue
        if not result:
            continue
        for item in result.get("verdicts", []):
            raw_id = str(item.get("id", ""))
            if not raw_id.startswith("R"):
                continue
            try:
                idx = int(raw_id[1:])
            except ValueError:
                continue
            if idx not in pending:
                continue
            verdict = item.get("verdict")
            if verdict not in ("confirmed", "absent"):
                continue
            req = pending.pop(idx)
            resolved[idx] = {
                "sentence": req.sentence, "page": req.page,
                "verdict": verdict, "reason": item.get("reason", ""),
                "chunks_checked": checked_count[idx],
            }
            if on_result:
                on_result(resolved[idx])

    out: list[dict] = []
    for idx, req in list(pending.items()):
        entry = {
            "sentence": req.sentence, "page": req.page, "verdict": "unclear",
            "reason": "ни один фрагмент текста РД не дал оснований для вывода",
            "chunks_checked": checked_count[idx],
        }
        resolved[idx] = entry
        if on_result:
            on_result(entry)
    for idx in sorted(resolved):
        out.append(resolved[idx])
    return out


def render_text_verify_report(results: list[dict]) -> str:
    lines = ["=== Семантическая сверка общих требований с текстом РД (Г.49) ===",
             f"Проверено требований: {len(results)}"]
    by_verdict: dict[str, list[dict]] = {}
    for r in results:
        by_verdict.setdefault(r["verdict"], []).append(r)
    for verdict in ("absent", "confirmed", "unclear"):
        group = by_verdict.get(verdict, [])
        if not group:
            continue
        lines.append(f"\n--- {verdict} ({len(group)}) ---")
        for r in group[:10]:
            lines.append(f"  стр.{r['page']}: {r['reason']}")
        if len(group) > 10:
            lines.append(f"  ... и ещё {len(group) - 10}")
    return "\n".join(lines)
