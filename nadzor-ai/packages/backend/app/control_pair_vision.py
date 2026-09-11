"""Дешёвая эскалация сильных пар листов в semantic vision для точек контроля.

Пайплайн сначала сопоставляет страницы детерминированно, затем использует
пиксельный предфильтр. В LLM уходят только уверенные drawing-пары, которые
реально отличаются по растру. Это не заменяет основной analysis-run, а даёт
триангуляции независимую пару доказательств: pixel_diff + semantic vision.
"""
from __future__ import annotations

import re
from typing import Sequence

from .llm import LlmConfig
from .matching import DocumentInput, match_page_pairs
from .triangulation import Signal
from .vision import compare_page_pair
from .visual_prefilter import DIFF_RATIO_THRESHOLD, visual_diff_ratio

_ROOM_NUMBER_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d+)?)(?!\d)")


def _room_keys(document: DocumentInput, page: int) -> set[str]:
    return {
        str(fact.get("key") or "").strip()
        for fact in document.room_facts
        if int(fact.get("page") or 0) == page and str(fact.get("key") or "").strip()
    }


def _mentioned_rooms(text: str, allowed: set[str]) -> set[str]:
    return {match for match in _ROOM_NUMBER_RE.findall(text or "") if match in allowed}


def run_targeted_pair_vision(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_pairs: int = 6,
) -> tuple[list[Signal], list[dict]]:
    """Проверить до ``max_pairs`` сильных визуально отличающихся drawing-пар.

    Возвращает сигналы и компактный диагностический список. ``pixel_diff``
    и ``vision_pair`` имеют общий domain/key, поэтому подтверждают сам факт
    изменения листа двумя независимыми механизмами. Room-сигнал от vision
    создаётся только когда модель явно назвала номер, который действительно
    есть на обеих страницах пары.
    """
    if max_pairs <= 0:
        return [], []

    pairs = match_page_pairs(list(before_docs), list(after_docs))
    strong = [
        pair for pair in pairs
        if pair.page_kind == "drawing"
        and pair.matched_by == "text"
        and not pair.discipline_mismatch
    ]
    strong.sort(key=lambda pair: (-pair.score, pair.before_file_idx, pair.before_page))

    signals: list[Signal] = []
    diagnostics: list[dict] = []
    llm_used = 0
    for pair in strong:
        if llm_used >= max_pairs:
            break
        before_path = before_paths[pair.before_file_idx]
        after_path = after_paths[pair.after_file_idx]
        try:
            ratio = visual_diff_ratio(before_path, pair.before_page, after_path, pair.after_page)
        except Exception as exc:  # noqa: BLE001 — одна пара не роняет прогон
            diagnostics.append({
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "score": pair.score,
                "status": "prefilter_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if ratio <= DIFF_RATIO_THRESHOLD:
            diagnostics.append({
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "score": pair.score,
                "diff_ratio": round(ratio, 4),
                "status": "visually_same",
            })
            continue

        llm_used += 1
        before_doc = before_docs[pair.before_file_idx]
        after_doc = after_docs[pair.after_file_idx]
        pair_key = (
            f"{pair.before_file_idx}:{pair.before_page}"
            f"->{pair.after_file_idx}:{pair.after_page}"
        )
        try:
            result = compare_page_pair(
                before_path,
                pair.before_page,
                after_path,
                pair.after_page,
                config,
                context=f"раздел {before_doc.discipline_code or '?'}; точка контроля",
                discipline=before_doc.discipline_code,
            )
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({
                "pair_key": pair_key,
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "score": pair.score,
                "diff_ratio": round(ratio, 4),
                "status": "vision_error",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        significant = result.get("significant") if isinstance(result, dict) else None
        significant = significant if isinstance(significant, list) else []
        changes = [
            str(item.get("change") or "").strip()
            for item in significant
            if isinstance(item, dict) and str(item.get("change") or "").strip()
        ]
        if not changes:
            diagnostics.append({
                "pair_key": pair_key,
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "score": pair.score,
                "diff_ratio": round(ratio, 4),
                "status": "no_semantic_change",
            })
            continue

        detail = " | ".join(changes[:5])
        # Два разных механизма подтверждают изменение именно этой пары листов.
        signals.append(Signal(
            source="pixel_diff", domain="page_pair", key=pair_key,
            detail=f"визуальное отличие растра: {ratio:.3f}",
        ))
        signals.append(Signal(
            source="vision_pair", domain="page_pair", key=pair_key, detail=detail,
        ))

        shared_rooms = _room_keys(before_doc, pair.before_page) & _room_keys(after_doc, pair.after_page)
        mentioned: set[str] = set()
        for item in significant:
            if not isinstance(item, dict):
                continue
            text = " ".join(
                str(item.get(field) or "")
                for field in ("label", "change", "field_check")
            )
            mentioned |= _mentioned_rooms(text, shared_rooms)
        for room in sorted(mentioned):
            signals.append(Signal(
                source="vision", domain="room", key=room, detail=detail,
            ))

        diagnostics.append({
            "pair_key": pair_key,
            "before_page": pair.before_page,
            "after_page": pair.after_page,
            "score": pair.score,
            "diff_ratio": round(ratio, 4),
            "status": "significant",
            "significant_total": len(changes),
            "rooms_mentioned": sorted(mentioned),
            "changes": changes[:5],
        })

    return signals, diagnostics
