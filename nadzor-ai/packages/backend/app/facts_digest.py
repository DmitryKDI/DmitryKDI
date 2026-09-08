"""ВЫЖИМКА разбора — то, что показывают инспектору вместо всего подряд.

Разбор тома — это тысячи строк: текст каждой страницы, все найденные
помещения, все позиции оборудования. Показать это целиком значит не
показать ничего: нужное тонет. Здесь из разбора собирается короткая
сводка — сколько листов и какие, что нашлось, чего не нашлось и почему, —
а подробности остаются в памяти и достаются постранично по требованию.

Правило одно: молчание не означает «чисто» (Г.10). Поэтому в выжимке
отдельно названы листы, по которым текстовым путём не нашлось ничего:
пустая строка «помещений нет» и «лист в кривых, текста нет вовсе» — разные
состояния, и второе требует просмотра изображения, а не вывода «всё в
порядке».
"""
from __future__ import annotations

from .documents import DocumentFacts

# Сколько примеров показывать в выжимке. Не порог истины: столько строк
# читается взглядом, дальше нужен постраничный просмотр.
EXAMPLES = 12


def _unique(values) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        text = str(value or "").strip()
        if text:
            seen.setdefault(text, None)
    return list(seen)


def digest_of(facts: DocumentFacts) -> dict:
    """Короткая сводка по разобранному документу."""
    kinds = facts.page_kinds or {}
    drawings = sorted(p for p, kind in kinds.items() if kind == "drawing")
    pages_with_text = {int(f["page"]) for f in facts.text_facts}
    silent = [p for p in drawings if p not in pages_with_text]

    rooms = _unique(f"{f.get('key')} {f.get('name') or ''}".strip()
                    for f in facts.room_facts)
    equipment = _unique(f.get("name") for f in facts.equipment_facts)
    sheets = _unique(
        " ".join(str(info.get(field) or "") for field in ("sheet_no", "sheet_name")).strip()
        for info in (facts.sheet_info or {}).values())
    systems = _unique(f.get("system_code") for f in facts.balance_facts)

    return {
        "name": facts.name,
        "pages": facts.pages,
        "drawings": len(drawings),
        "text_pages": len(pages_with_text),
        # Листы, где текстового слоя нет вовсе: подписи в кривых. По ним
        # текстовый путь не даёт ничего, и это не «чисто», а «не читано».
        "pages_without_text": len(silent),
        "pages_without_text_list": silent[:EXAMPLES],
        "excluded": len(facts.excluded or {}),
        "excluded_reasons": _unique((facts.excluded or {}).values())[:EXAMPLES],
        "rooms_total": len(rooms),
        "rooms": rooms[:EXAMPLES],
        "equipment_total": len(equipment),
        "equipment": equipment[:EXAMPLES],
        "sheets_total": len(sheets),
        "sheets": sheets[:EXAMPLES],
        "systems": systems[:EXAMPLES],
        "text_chars": sum(len(str(f.get("text") or "")) for f in facts.text_facts),
    }


def page_rows(facts: DocumentFacts) -> list[dict]:
    """Постранично: что именно нашлось на каждом листе.

    Это и есть «подробности по требованию» — строка на лист, без самого
    текста. Текст листа не отдаётся вовсе: в выжимке он не нужен, а
    гонять мегабайты в браузер ради подсчёта символов незачем.
    """
    kinds = facts.page_kinds or {}
    excluded = facts.excluded or {}
    sheet_info = facts.sheet_info or {}
    by_page: dict[int, dict] = {}
    for page in range(1, (facts.pages or 0) + 1):
        info = sheet_info.get(page) or {}
        by_page[page] = {
            "page": page,
            "kind": kinds.get(page, ""),
            "sheet_no": info.get("sheet_no") or "",
            "sheet_name": info.get("sheet_name") or "",
            "shifr": info.get("shifr") or "",
            "rooms": [],
            "equipment": 0,
            "chars": 0,
            "excluded": excluded.get(page, ""),
        }
    for fact in facts.text_facts:
        row = by_page.get(int(fact.get("page") or 0))
        if row is not None:
            row["chars"] += len(str(fact.get("text") or ""))
    for fact in facts.room_facts:
        row = by_page.get(int(fact.get("page") or 0))
        if row is not None and fact.get("key"):
            row["rooms"].append(str(fact["key"]))
    for fact in facts.equipment_facts:
        row = by_page.get(int(fact.get("page") or 0))
        if row is not None:
            row["equipment"] += 1
    for row in by_page.values():
        row["rooms"] = _unique(row["rooms"])
    return [by_page[page] for page in sorted(by_page)]
