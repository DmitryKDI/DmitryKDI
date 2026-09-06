"""Прицельная сверка графа маршрутизации ПД↔РД по заданным помещениям —
доводит `routing_graph.py` (Г.30 пп.3-5) до вызываемого инструмента, а не
только до библиотеки.

Почему это отдельный, прицельный путь, а не автоматический шаг по всем
страницам подряд: `build_routing_graph` измерен ~6 с/лист (Г.30) — прогон
по каждой странице каждого файла на комплекте в сотни листов физически
дорог. Вместо этого — по явно заданному списку номеров помещений: та же
дисциплина «точечно», что Г.30 п.5 формулирует для эскалации в целом.

Это единственный путь в проекте, способный увидеть находки КЛАССА 2
(Г.29) БЕЗ обращения к LLM: то же помещение присутствует и совпадает по
названию с обеих сторон, но трассировка (точка сбора, число
присоединённых веток) отличается — реестр присутствия/названия (rooms.py)
структурно слеп именно к этому классу, что и было эмпирически показано
на реальном комплекте (Г.29). `build_routing_graph`/`SegmentNetwork` —
чистая геометрия и текстовый слой PyMuPDF, LLM нигде не участвует.

Поиск кандидатных страниц-планов для помещения — та же логика, что уже
есть для vision-эскалации (Г.35/Г.40): сначала страницы, где номер
реально встречается в `room_facts` на `page_kind=drawing` (не в
экспликации — сам граф на таблице бессмыслен), резерв — страницы того же
этажа по отметке высоты (`level_pages.py`, Г.40), когда у помещения
известна только страница экспликации."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .classification import PAGE_KIND_DRAWING, open_pdf
from .documents import extract_document_facts
from .level_pages import build_level_fallback_index, level_fallback_candidates
from .routing_graph import RoutingEdge, build_routing_graph, diff_routing_graphs


def candidate_plan_pages(paths: list[str], room_keys: list[str]) -> dict[str, list[dict]]:
    """room_key -> [{path, page}] — только страницы `page_kind=drawing`, где
    номер реально встречается в `room_facts` (граф на экспликации не
    построить, там нет геометрии маршрутов), плюс, если таких страниц для
    помещения нет вообще, резерв по отметке этажа (Г.40) — план того же
    этажа как кандидат, а не окончательный факт."""
    room_index: dict[str, list[dict]] = {key: [] for key in room_keys}
    facts_by_path: dict[str, object] = {}
    for path in paths:
        try:
            facts_by_path[path] = extract_document_facts(path, path)
        except Exception:  # noqa: BLE001 — один битый файл не должен ронять поиск по остальным
            continue
    for path, facts in facts_by_path.items():
        for fact in facts.room_facts:  # type: ignore[attr-defined]
            key = fact.get("key")
            if key in room_index and facts.page_kinds.get(fact["page"]) == PAGE_KIND_DRAWING:  # type: ignore[attr-defined]
                room_index[key].append({"path": path, "page": fact["page"]})

    missing = [key for key in room_keys if not room_index[key]]
    if missing:
        room_levels, level_drawing_pages = build_level_fallback_index(paths)
        for key in missing:
            room_index[key] = level_fallback_candidates(key, room_levels, level_drawing_pages)
    return room_index


def build_edges_for_rooms(paths: list[str], room_keys: list[str], max_pages_per_room: int = 2) -> list[RoutingEdge]:
    """Строит графы маршрутизации по кандидатным страницам и собирает по
    ним рёбра заданных помещений. Первая страница, давшая РАЗРЕШЁННОЕ
    ребро для помещения, побеждает — дальше для этого помещения страницы
    не пробуются (тот же принцип "первый небезразличный результат
    останавливает перебор", что в vision_page_compare.check_visual_candidates,
    здесь без модели: "небезразлично" значит `resolved=True`).

    Помещение, для которого ни одна кандидатная страница не дала
    разрешённого ребра, всё равно попадает в результат — с `resolved=False`
    и причиной (Г.10: тишина не должна означать «маршрутов нет»)."""
    candidates = candidate_plan_pages(paths, room_keys)
    resolved: dict[str, RoutingEdge] = {}
    tried_pages: set[tuple[str, int]] = set()
    unresolved_reasons: dict[str, str] = {}

    for key in room_keys:
        for entry in candidates.get(key, [])[:max_pages_per_room]:
            if key in resolved:
                break
            page_ref = (entry["path"], entry["page"])
            if page_ref in tried_pages:
                continue
            tried_pages.add(page_ref)
            try:
                doc = open_pdf(entry["path"])
                try:
                    page = doc[entry["page"] - 1]
                    graph = build_routing_graph(page, room_keys=room_keys)
                finally:
                    doc.close()
            except Exception as exc:  # noqa: BLE001 — сбой одной страницы не должен ронять сверку остальных
                unresolved_reasons[key] = f"ошибка построения графа на {entry['path']} стр.{entry['page']}: {exc}"
                continue
            for edge in graph.edges:
                if edge.room_key == key and edge.resolved and key not in resolved:
                    resolved[key] = edge

    edges = list(resolved.values())
    for key in room_keys:
        if key in resolved:
            continue
        candidate_count = len(candidates.get(key, []))
        reason = unresolved_reasons.get(key) or (
            f"ни одна из {candidate_count} кандидатных страниц не дала разрешённого маршрута"
            if candidate_count else "кандидатных страниц-планов для этого помещения не найдено"
        )
        edges.append(RoutingEdge(branch_code="", room_key=key, resolved=False, reason=reason))
    return edges


def diff_room_routing(before_paths: list[str], after_paths: list[str], room_keys: list[str]) -> dict[str, list[dict]]:
    """Прицельная сверка графа маршрутизации ПД↔РД по заданным помещениям
    — без LLM. Возвращает тот же формат, что `routing_graph.diff_routing_graphs`
    (Г.30 п.3): renumbered/retargeted/connection_count_changed/unchanged/
    unusable/room_only_before/room_only_after."""
    before_edges = build_edges_for_rooms(before_paths, room_keys)
    after_edges = build_edges_for_rooms(after_paths, room_keys)
    return diff_routing_graphs(before_edges, after_edges)


def render_routing_diff_report(diff: dict[str, list[dict]]) -> str:
    lines = ["=== Прицельная сверка маршрутизации по заданным помещениям (Г.30, без LLM) ==="]
    labels = {
        "retargeted": "Маршрут ведёт к другой точке сбора",
        "connection_count_changed": "Другое число присоединений",
        "unusable": "Неразрешённый/неоднозначный маршрут — сравнивать не на чем",
        "room_only_before": "Помещение с разрешённым маршрутом только в ПД",
        "room_only_after": "Помещение с разрешённым маршрутом только в РД",
        "renumbered": "Перенумерация веток (не нарушение)",
        "unchanged": "Совпало полностью",
    }
    for key in ("retargeted", "connection_count_changed", "unusable",
                "room_only_before", "room_only_after", "renumbered", "unchanged"):
        entries = diff.get(key, [])
        if not entries:
            continue
        lines.append(f"\n--- {labels[key]} ({len(entries)}) ---")
        for entry in entries:
            room = entry.get("room_key", "?")
            if key in ("retargeted", "connection_count_changed", "renumbered", "unchanged"):
                lines.append(
                    f"  {room}: ПД ветки={entry.get('before_branches')} цели={entry.get('before_targets')} "
                    f"→ РД ветки={entry.get('after_branches')} цели={entry.get('after_targets')}"
                )
            elif key == "unusable":
                lines.append(f"  {room}: {entry.get('reason', '')}")
            else:
                lines.append(f"  {room}")
    return "\n".join(lines)
