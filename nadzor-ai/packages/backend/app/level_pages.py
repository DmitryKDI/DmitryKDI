"""Резервный поиск страниц плана по отметке этажа — продолжение Г.26/Г.35.

Найдено на реальном комплекте: `room_index` (Г.35/Г.26) строится из
`room_facts`, а `room_facts` НАМЕРЕННО не содержит голых номеров-подписей с
плана (Г.14 — как реестр это чистый шум). На части документов это не мешает:
экспликация и план — разные зоны одного листа или соседние страницы. На
другом комплекте экспликации и планы физически разнесены по документу (план
— отдельная страница за много листов от своей экспликации), и тогда
`_candidate_pages` находит только страницы экспликации (таблицу), а сам план
с условными обозначениями и графикой вообще не попадает в кандидаты.

Отметка высоты этажа (`+X.XXX`/`-X.XXX`) — рабочий резервный якорь именно
для этого случая: в отличие от номера помещения на плане, она почти всегда
остаётся в тексте даже там, где остальные подписи плана переведены в кривые
(тот же класс проблемы, что Г.8/Г.13 разбирают для штампа, только для
подписи уровня) — потому что штамп/заголовок листа обычно не переведён в
кривые целиком. Отметка не доказывает, что нужное помещение есть на
странице — только что страница того же этажа; решает по-прежнему зрение
(`vision_page_compare.check_requirement_on_page`), эти кандидаты лишь
добавляются в общий список наравне с найденными по номеру."""
from __future__ import annotations

import re
from collections import defaultdict

from .classification import PAGE_KIND_DRAWING
from .documents import extract_document_facts

_LEVEL_RE = re.compile(r"[+-]\d{1,2}\.\d{3}")


def extract_levels(text: str) -> set[str]:
    """Отметки высоты, упомянутые на странице (может быть несколько)."""
    return set(_LEVEL_RE.findall(text))


def build_level_fallback_index(paths: list[str]) -> tuple[dict[str, set[str]], dict[str, list[dict]]]:
    """room_levels: room_key -> отметки, встреченные на страницах экспликации
    этого помещения (обычно в заголовке типа «2 этаж +1.750»).

    level_drawing_pages: отметка -> страницы с page_kind=drawing, где та же
    отметка упомянута текстом — кандидаты на план того же этажа.

    Файл, который не удалось прочитать, пропускается молча для этой функции
    (не основной путь — `_registry` в вызывающем коде уже выводит
    предупреждение по тому же файлу)."""
    room_levels: dict[str, set[str]] = defaultdict(set)
    level_drawing_pages: dict[str, list[dict]] = defaultdict(list)
    for path in paths:
        try:
            facts = extract_document_facts(path, path)
        except Exception:  # noqa: BLE001 — уже залогировано вызывающим кодом через _registry
            continue
        text_by_page = {f["page"]: f["text"] for f in facts.text_facts}
        for fact in facts.room_facts:
            page_text = text_by_page.get(fact["page"], "")
            room_levels[fact["key"]] |= extract_levels(page_text)
        for page, text in text_by_page.items():
            if facts.page_kinds.get(page) != PAGE_KIND_DRAWING:
                continue
            for level in extract_levels(text):
                level_drawing_pages[level].append({"path": path, "page": page})
    return dict(room_levels), dict(level_drawing_pages)


def level_fallback_candidates(
    room_key: str,
    room_levels: dict[str, set[str]],
    level_drawing_pages: dict[str, list[dict]],
) -> list[dict]:
    """Страницы-кандидаты для помещения по совпадению отметки этажа — не
    факт, что помещение реально на них есть, только совпадение уровня."""
    seen: set[tuple[str, int]] = set()
    pages: list[dict] = []
    for level in room_levels.get(room_key, ()):
        for entry in level_drawing_pages.get(level, ()):
            key = (entry["path"], entry["page"])
            if key in seen:
                continue
            seen.add(key)
            pages.append(entry)
    return pages


def augment_room_index_with_level_fallback(
    room_index: dict[str, list[dict]],
    after_paths: list[str],
) -> dict[str, list[dict]]:
    """Дополняет `room_index` (Г.35/Г.26, `{room_key: [{path, page, ...}]}`)
    страницами-кандидатами по отметке этажа, для помещений, у которых
    отметка вообще известна (есть хотя бы одна страница экспликации).
    Резервные кандидаты добавляются ПОСЛЕ уже найденных по номеру —
    `_candidate_pages` (round-robin по глубине) попробует их только если
    бюджет листов на находку не исчерпан настоящими совпадениями."""
    room_levels, level_drawing_pages = build_level_fallback_index(after_paths)
    for room_key in room_levels:
        fallback = level_fallback_candidates(room_key, room_levels, level_drawing_pages)
        if fallback:
            room_index[room_key] = room_index.get(room_key, []) + fallback
    return room_index
