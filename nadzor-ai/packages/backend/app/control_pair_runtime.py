from __future__ import annotations

import hashlib
import os
import re
from typing import Sequence

from .anchors import normalize_room_key
from .control_pair_candidates import candidate_pairs, page_text
from .entity_control import run_room_entity_controls
from .focused_pair_vision import MAX_FOCUSED_ROOM_CALLS, compare_shared_rooms_focused
from .llm import LlmConfig, call_llm_json
from .matching import DocumentInput
from .triangulation import Signal
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url
from .visual_prefilter import visual_change_evidence

_ROOM_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d+)?)(?!\d)")


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_GRAPHICAL_CANDIDATE_PAIRS = _int_env("NADZOR_MAX_GRAPHICAL_CANDIDATE_PAIRS", 18, 1, 60)

_SCOPE_PROMPT = f"""Ты сравниваешь инженерные решения ПД и РД/ИД вслепую. Нужны только наблюдаемые инженерные различия, без legal/severity. Две картинки могут быть разными по типу: схема и план. В таком случае сравнивай сущности, маркировки, параметры, подключения и направление, а не координаты. Проверь inventory, topology, connections, parameters. Не считай различием рамку, масштаб, мебель, название помещения или качество рендера. При видимом основании сохрани candidate difference; сомнение опиши в unclear_reason.

{UNTRUSTED_INPUT_RULE}

Ответ только JSON: {{"comparable":true,"differences":[{{"label":"кратко","change":"наблюдаемое инженерное отличие","rooms":["101"],"pd_observation":"что видно","rd_observation":"что видно"}}],"unclear_reason":""}}"""


def semantic_scope_compare(before_path: str, before_page: int, after_path: str, after_page: int, before_doc: DocumentInput, after_doc: DocumentInput, shared_rooms: Sequence[str], config: LlmConfig, clip=None) -> dict:
    before_img = render_page_to_data_url(before_path, before_page, clip_frac=clip)
    after_img = render_page_to_data_url(after_path, after_page, clip_frac=clip)
    before_text, after_text = page_text(before_doc, before_page), page_text(after_doc, after_page)
    room_text = ", ".join(shared_rooms[:60]) or "не извлечены"
    text = (
        f"Общие помещения/зоны: {room_text}. Раздел ПД: {before_doc.discipline_code or 'unknown'}; РД: {after_doc.discipline_code or 'unknown'}.\n"
        f"ПД text: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{before_text[:3000]}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"РД text: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{after_text[:3000]}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"
    )
    digest = hashlib.sha256(f"{before_path}:{before_page}:{after_path}:{after_page}:{clip}:{room_text}".encode()).hexdigest()
    out = call_llm_json(config, _SCOPE_PROMPT, text, images=[before_img, after_img], operation="vision", source_digest=digest, prompt_version="blind-scope-v5-advisory-raster")
    return out if isinstance(out, dict) else {}


def _difference_items(result: dict) -> list[dict]:
    raw = result.get("differences") if isinstance(result, dict) else []
    return [x for x in raw if isinstance(x, dict) and str(x.get("change") or "").strip()] if isinstance(raw, list) else []


def _mentioned_rooms(items: Sequence[dict], allowed: set[str]) -> set[str]:
    found = set()
    for item in items:
        for room in item.get("rooms") or []:
            key = normalize_room_key(room)
            if key in allowed:
                found.add(key)
        text = " ".join(str(item.get(k) or "") for k in ("label", "change", "pd_observation", "rd_observation"))
        for raw in _ROOM_NUMBER_RE.findall(text):
            key = normalize_room_key(raw)
            if key in allowed:
                found.add(key)
    return found


def run_targeted_pair_vision(before_docs: Sequence[DocumentInput], after_docs: Sequence[DocumentInput], before_paths: Sequence[str], after_paths: Sequence[str], config: LlmConfig, *, max_pairs: int = 6):
    signals, diagnostics = [], []
    entity_signals, entity_diags = run_room_entity_controls(before_docs, after_docs, before_paths, after_paths, config)
    signals.extend(entity_signals)
    diagnostics.extend([{"control_type": "room_entity", **x} for x in entity_diags])
    if max_pairs <= 0:
        return signals, diagnostics

    all_pairs = candidate_pairs(before_docs, after_docs)
    pairs = all_pairs[:MAX_GRAPHICAL_CANDIDATE_PAIRS]
    focused_used = whole_used = raster_significant = 0

    for pair in pairs:
        before_path, after_path = before_paths[pair.before_file_idx], after_paths[pair.after_file_idx]
        before_doc, after_doc = before_docs[pair.before_file_idx], after_docs[pair.after_file_idx]
        try:
            evidence = visual_change_evidence(before_path, pair.before_page, after_path, pair.after_page)
        except Exception as exc:
            evidence = {"significant": False, "error": f"{type(exc).__name__}: {exc}"}

        base = {
            "control_type": "page_pair", "pair_key": pair.key, "matched_by": pair.matched_by,
            "before_page": pair.before_page, "after_page": pair.after_page,
            "before_file_index": pair.before_file_idx, "after_file_index": pair.after_file_idx,
            "score": round(pair.score, 6), "rooms_shared": list(pair.shared_rooms),
            "diff_ratio": evidence.get("diff_ratio"), "raw_diff_ratio": evidence.get("raw_diff_ratio"),
            "changed_cells": evidence.get("changed_cells"), "hot_zone": evidence.get("hot_zone"),
            "alignment": evidence.get("alignment"), "raster_significant": bool(evidence.get("significant")),
        }
        if evidence.get("significant"):
            raster_significant += 1
            signals.append(Signal("raster_diff", "page_pair", pair.key, f"структурный raster hint: ratio={evidence.get('diff_ratio')}"))

        items = []
        if pair.shared_rooms and focused_used < MAX_FOCUSED_ROOM_CALLS:
            remaining = MAX_FOCUSED_ROOM_CALLS - focused_used
            focused_items, focused_diags, used = compare_shared_rooms_focused(
                before_path, pair.before_page, after_path, pair.after_page,
                pair.shared_rooms, config, max_calls=remaining,
                discipline=before_doc.discipline_code or after_doc.discipline_code or "",
            )
            focused_used += used
            items.extend(focused_items)
            diagnostics.extend([{**base, "control_type": "room_focus", **x} for x in focused_diags])

        result = {}
        if not items and whole_used < max_pairs:
            whole_used += 1
            clip_raw = evidence.get("hot_zone") if evidence.get("significant") and evidence.get("local_cluster") else None
            clip = tuple(clip_raw) if clip_raw else None
            try:
                result = semantic_scope_compare(before_path, pair.before_page, after_path, pair.after_page, before_doc, after_doc, pair.shared_rooms, config, clip=clip)
                items.extend(_difference_items(result))
            except Exception as exc:
                diagnostics.append({**base, "control_type": "whole_page_vision", "status": "vision_error", "error": f"{type(exc).__name__}: {exc}"})

        if not items:
            diagnostics.append({**base, "status": "no_semantic_change", "unclear_reason": str(result.get("unclear_reason") or ""), "focused_calls_total": focused_used, "whole_page_calls_total": whole_used})
            continue

        detail = " | ".join(str(x.get("change") or "").strip() for x in items[:6]) or "наблюдаемое инженерное различие"
        signals.append(Signal("vision_pair", "page_pair", pair.key, detail))
        rooms = _mentioned_rooms(items, set(pair.shared_rooms))
        for room in sorted(rooms):
            signals.append(Signal("vision", "room", room, detail))
        diagnostics.append({**base, "status": "significant", "differences_total": len(items), "rooms_mentioned": sorted(rooms), "focused_calls_total": focused_used, "whole_page_calls_total": whole_used})

    diagnostics.append({
        "control_type": "coverage", "status": "complete" if len(all_pairs) <= len(pairs) else "candidate_budget",
        "candidate_pairs_total": len(all_pairs), "candidate_pairs_checked": len(pairs),
        "raster_significant_pairs": raster_significant, "whole_page_calls_used": whole_used,
        "focused_calls_used": focused_used, "max_focused_calls": MAX_FOCUSED_ROOM_CALLS,
        "max_candidate_pairs": MAX_GRAPHICAL_CANDIDATE_PAIRS,
    })
    return signals, diagnostics
