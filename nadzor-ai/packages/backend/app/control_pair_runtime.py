from __future__ import annotations

import hashlib
import os
import re
from typing import Sequence

from .anchors import normalize_room_key
from .control_pair_candidates import candidate_pairs, page_text
from .entity_control import run_room_entity_controls
from .focused_pair_vision import MAX_FOCUSED_ROOM_CALLS, compare_shared_rooms_focused
from .generic_region_vision import MAX_GENERIC_REGION_CALLS, compare_non_room_regions
from .llm import LlmConfig, call_llm_json
from .matching import DocumentInput
from .triangulation import Signal
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url
from .vision_page_compare import normalize_regions
from .visual_prefilter import visual_change_evidence

_ROOM_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,4}(?:\.\d+)?)(?!\d)")


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_GRAPHICAL_CANDIDATE_PAIRS = _int_env("NADZOR_MAX_GRAPHICAL_CANDIDATE_PAIRS", 18, 1, 60)

# Сколько увеличений по зонам, названным моделью, разрешено на одну пару
# листов. Бюджет просмотра, а не граница истины: круги обхода нарезаются
# по этому числу, поэтому вторая зона пары ждёт, пока первую зону получат
# все остальные пары.
MAX_REGION_ZOOMS_PER_PAIR = _int_env("NADZOR_MAX_REGION_ZOOMS_PER_PAIR", 2, 0, 6)

_SCOPE_PROMPT = f"""Ты сравниваешь инженерные решения ПД и РД/ИД вслепую. Нужны только наблюдаемые инженерные различия, без legal/severity. Две картинки могут быть разными по типу: схема и план. В таком случае сравнивай сущности, маркировки, параметры, подключения и направление, а не координаты. Проверь inventory, topology, connections, parameters. Не считай различием рамку, масштаб, мебель, название помещения или качество рендера. При видимом основании сохрани candidate difference; сомнение опиши в unclear_reason.

{UNTRUSTED_INPUT_RULE}

Отдельно оцени `comparability` — можно ли ВООБЩЕ судить по показанному: "high" — инженерная графика видна и читается; "medium" — видна частично, мелко или перекрыта; "low" — показаны только таблица, экспликация, штамп или пустая область.

Если нужно рассмотреть место ближе, назови зоны в `candidate_regions`: координаты долями листа от левого верхнего угла, [x1,y1,x2,y2], каждое число от 0 до 1. Проси увеличение и тогда, когда ответить уверенно не получилось: это способ доработать, а не признание неудачи. Если увеличение не нужно, верни пустой список.

Ответ только JSON: {{"comparable":true,"comparability":"high|medium|low","differences":[{{"label":"кратко","change":"наблюдаемое инженерное отличие","rooms":["101"],"pd_observation":"что видно","rd_observation":"что видно"}}],"candidate_regions":[{{"reason":"что там рассмотреть","rd_bbox_norm":[0.0,0.0,1.0,1.0],"priority":"high|medium|low"}}],"unclear_reason":""}}"""


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
    out = call_llm_json(config, _SCOPE_PROMPT, text, images=[before_img, after_img], operation="vision", source_digest=digest, prompt_version="blind-scope-v6-unified-contract")
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


def _comparability(result: dict) -> str:
    """Насколько вообще можно судить по показанному.

    Поле может не прийти вовсе (старый ответ нёс только булево
    `comparable`) — тогда это «неизвестно», то есть `medium`, а не
    «читаемо»: молчание не повышает уверенность (Г.10).
    """
    value = str(result.get("comparability") or "").strip().casefold()
    if value in ("high", "medium", "low"):
        return value
    return "low" if result.get("comparable") is False else "medium"


class _PairState:
    """Что известно о паре листов после просмотра целиком."""

    def __init__(self, pair, before_path, after_path, before_doc, after_doc, base):
        self.pair = pair
        self.before_path, self.after_path = before_path, after_path
        self.before_doc, self.after_doc = before_doc, after_doc
        self.base = base
        self.items: list[dict] = []
        self.regions: list[dict] = []
        self.regions_seen = 0
        self.semantic_path = ""
        self.comparability = "low"
        self.whole_page_done = False
        self.unclear_reason = ""

    @property
    def needs_zoom(self) -> bool:
        """Увеличение нужно, пока есть что уточнять.

        Чистый и читаемый лист переспрашивать не за чем: увеличение —
        способ разобрать подозрение или нечитаемое место, а не ритуал на
        каждой паре. Всё остальное — повод посмотреть ближе.
        """
        return not (self.whole_page_done and self.comparability == "high"
                    and not self.items)

    def status(self) -> tuple[str, str]:
        """«Различий нет» — вывод, а не тишина (раздел 13 задания).

        Он записывается, только когда лист действительно смотрели и
        читаемость это позволяла. Сорванный вызов, нечитаемый лист и
        неизрасходованный бюджет дают другое состояние, с причиной.
        """
        if self.items:
            return "significant", self.unclear_reason
        if not self.whole_page_done:
            return "not_compared", self.unclear_reason or "лист целиком не просмотрен"
        if self.comparability == "low":
            return "not_compared", self.unclear_reason or "на листе не видно инженерной графики"
        unseen = [x for x in self.regions[self.regions_seen:]
                  if x.get("priority") == "high"]
        if unseen:
            return "not_compared", "названные моделью зоны осмотрены не все"
        return "compared_no_candidate", self.unclear_reason


def _look_whole_page(state: _PairState, config: LlmConfig, clip=None) -> dict:
    """Один просмотр пары листов целиком либо её адресного фрагмента."""
    pair = state.pair
    return semantic_scope_compare(
        state.before_path, pair.before_page, state.after_path, pair.after_page,
        state.before_doc, state.after_doc, pair.shared_rooms, config, clip=clip)


def run_targeted_pair_vision(before_docs: Sequence[DocumentInput], after_docs: Sequence[DocumentInput], before_paths: Sequence[str], after_paths: Sequence[str], config: LlmConfig, *, max_pairs: int = 6):
    """Сравнить пары листов: сначала каждую целиком, потом спорные — ближе.

    Порядок здесь и есть суть шага. Раньше сравнение начиналось с якорного
    пути (помещения, затем прочие якоря), а лист целиком смотрелся только
    `if not items` — то есть стоило якорному пути найти что-нибудь, и модель
    не видела лист целиком НИКОГДА. Признак сопоставления решал не «куда
    смотреть раньше», а «смотреть ли вообще».

    Теперь два прохода. Первый — обзорный: каждая принятая пара
    просматривается целиком. Второй — уточняющий: увеличение по зонам,
    которые назвала модель, и только потом якорный путь. Проходы
    разделены ещё и ради честного распределения бюджета: пока хоть одна
    пара не осмотрена целиком, ни одна не получает увеличений (раздел 10
    задания).
    """
    signals, diagnostics = [], []
    entity_signals, entity_diags = run_room_entity_controls(before_docs, after_docs, before_paths, after_paths, config)
    signals.extend(entity_signals)
    diagnostics.extend([{"control_type": "room_entity", **x} for x in entity_diags])
    if max_pairs <= 0:
        return signals, diagnostics

    all_pairs = candidate_pairs(before_docs, after_docs)
    pairs = all_pairs[:MAX_GRAPHICAL_CANDIDATE_PAIRS]
    focused_used = generic_used = whole_used = raster_significant = zoom_used = 0
    states: list[_PairState] = []

    # Проход 1 — обзорный: каждая пара целиком.
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
            "anchors_shared": list(getattr(pair, "shared_anchors", ()) or ()),
            "evidence_tier": getattr(pair, "evidence_tier", ""),
            "diff_ratio": evidence.get("diff_ratio"), "raw_diff_ratio": evidence.get("raw_diff_ratio"),
            "changed_cells": evidence.get("changed_cells"), "hot_zone": evidence.get("hot_zone"),
            "alignment": evidence.get("alignment"), "raster_significant": bool(evidence.get("significant")),
        }
        if evidence.get("significant"):
            raster_significant += 1
            signals.append(Signal("raster_diff", "page_pair", pair.key, f"структурный raster hint: ratio={evidence.get('diff_ratio')}"))

        state = _PairState(pair, before_path, after_path, before_doc, after_doc, base)
        states.append(state)
        if whole_used >= max_pairs:
            state.unclear_reason = "бюджет обзорного просмотра исчерпан до этой пары"
            continue

        whole_used += 1
        try:
            result = _look_whole_page(state, config)
        except Exception as exc:
            diagnostics.append({**base, "control_type": "whole_page_vision",
                                "status": "vision_error", "error": f"{type(exc).__name__}: {exc}"})
            state.unclear_reason = f"вызов не выполнен: {type(exc).__name__}: {exc}"
            continue
        result = result if isinstance(result, dict) else {}
        state.whole_page_done = True
        state.comparability = _comparability(result)
        state.unclear_reason = str(result.get("unclear_reason") or "")
        state.regions = normalize_regions(result.get("candidate_regions"))
        whole_items = _difference_items(result)
        if whole_items:
            state.items.extend(whole_items)
            state.semantic_path = "whole_page"

    # Проход 2 — уточняющий: сначала по кругу одно увеличение на пару, и
    # только исчерпав зоны от модели, якорный путь. Круги, а не подряд:
    # иначе первая же пара выбрала бы весь бюджет.
    zoom_budget = max_pairs * MAX_REGION_ZOOMS_PER_PAIR
    for round_index in range(MAX_REGION_ZOOMS_PER_PAIR):
        for state in states:
            if zoom_used >= zoom_budget:
                break
            if not state.needs_zoom or round_index >= len(state.regions):
                continue
            region = state.regions[round_index]
            zoom_used += 1
            try:
                closer = _look_whole_page(state, config, clip=region["rd_bbox_norm"])
            except Exception as exc:
                diagnostics.append({**state.base, "control_type": "whole_page_vision",
                                    "status": "vision_error", "zoom": True,
                                    "error": f"{type(exc).__name__}: {exc}"})
                continue
            closer = closer if isinstance(closer, dict) else {}
            state.regions_seen = round_index + 1
            if _comparability(closer) != "low":
                state.comparability = max(state.comparability, _comparability(closer),
                                          key=lambda v: {"low": 0, "medium": 1, "high": 2}[v])
            closer_items = _difference_items(closer)
            if closer_items:
                state.items.extend(closer_items)
                state.semantic_path = state.semantic_path or "model_region_zoom"

    # Якорный путь — последний источник зон увеличения, а не отдельный
    # конвейер: помещение и прочие якоря говорят, КУДА смотреть ближе, и
    # никогда — сравнивать ли вообще.
    for state in states:
        if not state.needs_zoom:
            continue
        pair = state.pair
        discipline = state.before_doc.discipline_code or state.after_doc.discipline_code or ""
        if pair.shared_rooms and focused_used < MAX_FOCUSED_ROOM_CALLS:
            focused_items, focused_diags, used = compare_shared_rooms_focused(
                state.before_path, pair.before_page, state.after_path, pair.after_page,
                pair.shared_rooms, config, max_calls=MAX_FOCUSED_ROOM_CALLS - focused_used,
                discipline=discipline,
            )
            focused_used += used
            state.items.extend(focused_items)
            if focused_items:
                state.semantic_path = state.semantic_path or "room_focus"
            diagnostics.extend([{**state.base, "control_type": "room_focus", **x} for x in focused_diags])
        elif generic_used < MAX_GENERIC_REGION_CALLS:
            generic_items, generic_diags, used = compare_non_room_regions(
                state.before_path, pair.before_page, state.after_path, pair.after_page,
                getattr(pair, "shared_anchors", ()) or (), config,
                max_calls=MAX_GENERIC_REGION_CALLS - generic_used,
                discipline=discipline,
            )
            generic_used += used
            state.items.extend(generic_items)
            if generic_items:
                state.semantic_path = state.semantic_path or "generic_region"
            diagnostics.extend([{**state.base, "control_type": "comparison_region", **x} for x in generic_diags])

    for state in states:
        status, unclear_reason = state.status()
        record = {
            **state.base, "status": status, "unclear_reason": unclear_reason,
            "comparability": state.comparability,
            "whole_page_done": state.whole_page_done,
            "focused_calls_total": focused_used,
            "generic_region_calls_total": generic_used,
            "whole_page_calls_total": whole_used,
            "region_zoom_calls_total": zoom_used,
        }
        if not state.items:
            diagnostics.append(record)
            continue
        detail = " | ".join(str(x.get("change") or "").strip() for x in state.items[:6]) or "наблюдаемое инженерное различие"
        signals.append(Signal("vision_pair", "page_pair", state.pair.key, detail))
        rooms = _mentioned_rooms(state.items, set(state.pair.shared_rooms))
        for room in sorted(rooms):
            signals.append(Signal("vision", "room", room, detail))
        diagnostics.append({**record, "semantic_path": state.semantic_path,
                            "differences_total": len(state.items),
                            "rooms_mentioned": sorted(rooms)})

    diagnostics.append({
        "control_type": "coverage", "status": "complete" if len(all_pairs) <= len(pairs) else "candidate_budget",
        "candidate_pairs_total": len(all_pairs), "candidate_pairs_checked": len(pairs),
        "raster_significant_pairs": raster_significant, "whole_page_calls_used": whole_used,
        "region_zoom_calls_used": zoom_used, "max_region_zooms_per_pair": MAX_REGION_ZOOMS_PER_PAIR,
        "focused_calls_used": focused_used, "max_focused_calls": MAX_FOCUSED_ROOM_CALLS,
        "generic_region_calls_used": generic_used, "max_generic_region_calls": MAX_GENERIC_REGION_CALLS,
        "max_candidate_pairs": MAX_GRAPHICAL_CANDIDATE_PAIRS,
    })
    return signals, diagnostics
