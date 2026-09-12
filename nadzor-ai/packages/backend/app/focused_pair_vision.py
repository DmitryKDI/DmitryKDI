"""Room-focused blind drawing comparison tuned for factual recall.

This stage asks the vision model to do one narrow job: describe observable
engineering differences between PD and RD/ID.  It deliberately avoids legal
qualification, severity and field actions.  The default is one ROOM per model
call because dense multi-room montages were a recurring source of false
negatives.  Candidate rooms are ranked by a cheap local raster score first so
LLM budget is spent on the most visually changed zones without benchmark hints.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Sequence

import pymupdf

from .anchors import normalize_room_key
from .llm import LlmConfig, call_llm_json, png_bytes_to_data_url
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_png_bytes
from .vision_page_compare import room_label_crops


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# GigaChat peer review recommended one room per vision call and at most two.
# We keep a larger global budget but cap each pair so early dense sheets cannot
# starve later candidate pairs.
MAX_FOCUSED_ROOM_CALLS = _positive_int_env("NADZOR_MAX_FOCUSED_ROOM_VISION_CALLS", 24, 0, 64)
MAX_FOCUSED_CALLS_PER_PAIR = _positive_int_env("NADZOR_MAX_FOCUSED_CALLS_PER_PAIR", 4, 1, 8)
FOCUSED_ROOMS_PER_CALL = _positive_int_env("NADZOR_FOCUSED_ROOMS_PER_CALL", 1, 1, 2)
MAX_FOCUSED_ROOMS_PER_PAIR = _positive_int_env("NADZOR_MAX_FOCUSED_ROOMS_PER_PAIR", 16, 1, 40)
FOCUSED_CROP_PADDING = _float_env("NADZOR_FOCUSED_CROP_PADDING", 0.15, 0.0, 0.40)

_ROOM_NUM_RE = re.compile(r"^(\d{1,4})(?:\.(\d+))?([а-яё]?)$", re.IGNORECASE)
_STATUS_PRIORITY = {"same": 0, "unclear": 1, "changed": 2}
_READABILITY = {"good", "partial", "poor"}
_REQUIRED_COVERAGE = {"inventory", "topology", "connections", "parameters"}
_ALLOWED_KINDS = {"layout", "equipment", "spec", "annotation"}

_ARCHITECTURAL_PHRASES = (
    "добавлено помещение",
    "добавлен медицинский пункт",
    "удалено помещение",
    "назначение помещения",
    "переименовано помещение",
    "room added",
    "room removed",
    "room renamed",
)
_ENGINEERING_STEMS = (
    "вент", "воздух", "дефлект", "решет", "клапан", "приточ", "вытяж",
    "отоп", "радиатор", "труб", "стояк", "тепл", "нагрев", "охлаж",
    "кондиц", "оборуд", "установ", "насос", "теплообмен", "канал",
    "кабел", "элект", "свет", "розет", "вод", "спринкл", "пожар",
    "датчик", "система", "трасс", "коммуникац", "арматур", "вентилят",
    "диаметр", "сечение", "уклон", "подключ", "ветк", "фланц", "опор",
    "duct", "pipe", "fan", "heater", "radiator", "equipment", "hvac",
)

_FOCUSED_ROOM_PROMPT = f"""\
Ты — vision-аналитик инженерных чертежей. Сравниваешь ПД -> РД/ИД вслепую.
На этом этапе нужна ТОЛЬКО фиксация наблюдаемых инженерных различий. Не делай
юридических выводов, не оценивай severity и не предлагай действия инспектору.

Вход — одно paired-изображение: для каждого ROOM слева фрагмент ПД, справа
соответствующий фрагмент РД/ИД. Обычно показан один ROOM. Сравнивай только
левую и правую часть одного ROOM.

Для КАЖДОГО ROOM выполни последовательность и не завершай анализ раньше:
1) оцени читаемость обеих сторон;
2) составь инвентарь видимых инженерных элементов: воздуховоды, решётки,
   клапаны, оборудование, трубопроводы, стояки, арматура, приборы и т.п.;
3) проверь топологию и непрерывность трасс внутри зоны;
4) проверь точки входа/выхода и подключения к стоякам/оборудованию;
5) проверь параметры и обозначения: диаметр/сечение, тип, марку, стрелки,
   тип линии, монтаж/демонтаж, если это читается;
6) только после этих шагов выбери changed / same / unclear.

Правила статуса:
- changed — есть конкретное инженерное отличие, которое можно назвать;
- same — ОБЕ стороны хорошо читаемы и все четыре категории coverage
  inventory/topology/connections/parameters реально проверены;
- unclear — хотя бы одна сторона читается частично/плохо, зона обрезана,
  показана таблица/экспликация либо остаётся неоднозначность. Не превращай
  отсутствие уверенности в same.

Игнорируй сами по себе: изменение названия/назначения помещения, мебель,
отделку, стены/перегородки, штамп, рамку, масштаб, цвет, шрифт и качество
рендера. Не считай отсутствием инженерного элемента только отсутствие слова.
Если элемент есть в одной версии и отсутствует в другой — это changed, если
обе зоны действительно сопоставимы.

Для changed верни differences[] и разделяй разные сущности. kind:
layout — трасса/топология/подключение; equipment — наличие/тип оборудования;
spec — диаметр/сечение/марка/параметр; annotation — инженерная подпись.
bbox_pd/bbox_rd необязательны; если даёшь, это [x0,y0,x1,y1] в диапазоне 0..1
относительно соответствующей половины paired-изображения. Не выдумывай bbox.

{UNTRUSTED_INPUT_RULE}

Отвечай только JSON:
{{"checked_rooms":[{{
  "room":"101",
  "readability_pd":"good|partial|poor",
  "readability_rd":"good|partial|poor",
  "coverage":["inventory","topology","connections","parameters"],
  "pd_observation":"кратко: что инженерного видно в ПД",
  "rd_observation":"кратко: что инженерного видно в РД/ИД",
  "differences":[{{
    "kind":"layout|equipment|spec|annotation",
    "summary_ru":"конкретное инженерное отличие",
    "bbox_pd":[0.1,0.2,0.3,0.4],
    "bbox_rd":[0.1,0.2,0.3,0.4]
  }}],
  "status":"changed|same|unclear"
}}],
"summary":"кратко, только наблюдаемые инженерные различия"}}
"""


def room_sort_key(room: str):
    key = normalize_room_key(room)
    match = _ROOM_NUM_RE.fullmatch(key)
    if not match:
        return (1, key)
    return (0, int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").casefold())


def select_rooms(shared_rooms: Sequence[str]) -> list[str]:
    """Keep room coverage broad instead of always taking the first numbers."""
    rooms = sorted(
        {normalize_room_key(room) for room in shared_rooms if normalize_room_key(room)},
        key=room_sort_key,
    )
    limit = MAX_FOCUSED_ROOMS_PER_PAIR
    if len(rooms) <= limit:
        return rooms
    if limit == 1:
        return [rooms[len(rooms) // 2]]
    indexes = {
        int(round(i * (len(rooms) - 1) / (limit - 1)))
        for i in range(limit)
    }
    return [rooms[index] for index in sorted(indexes)]


def _expand_clip(clip: tuple[float, float, float, float], pad_ratio: float = FOCUSED_CROP_PADDING):
    x0, y0, x1, y1 = clip
    width = max(0.001, x1 - x0)
    height = max(0.001, y1 - y0)
    pad_x = width * pad_ratio
    pad_y = height * pad_ratio
    return (
        max(0.0, x0 - pad_x),
        max(0.0, y0 - pad_y),
        min(1.0, x1 + pad_x),
        min(1.0, y1 + pad_y),
    )


def _pixel_gray(pixel) -> float:
    if not pixel:
        return 255.0
    if len(pixel) == 1:
        return float(pixel[0])
    return sum(float(value) for value in pixel[:3]) / min(3, len(pixel))


def _png_grid(png: bytes, grid: int = 14) -> list[list[float]]:
    pix = pymupdf.Pixmap(png)
    out: list[list[float]] = []
    for gy in range(grid):
        row: list[float] = []
        y = min(pix.height - 1, max(0, int((gy + 0.5) / grid * pix.height)))
        for gx in range(grid):
            x = min(pix.width - 1, max(0, int((gx + 0.5) / grid * pix.width)))
            row.append(255.0 - _pixel_gray(pix.pixel(x, y)))
        out.append(row)
    return out


def _aligned_grid_difference(before_png: bytes, after_png: bytes) -> float:
    """Cheap local score robust to small crop shifts and modest scale drift."""
    before = _png_grid(before_png)
    after = _png_grid(after_png)
    grid = len(before)
    center = (grid - 1) / 2.0
    best = math.inf
    for scale in (0.90, 1.0, 1.10):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                total = 0.0
                count = 0
                for y in range(grid):
                    ay = int(round(center + (y - center) * scale + dy))
                    if ay < 0 or ay >= grid:
                        continue
                    for x in range(grid):
                        ax = int(round(center + (x - center) * scale + dx))
                        if ax < 0 or ax >= grid:
                            continue
                        total += abs(before[y][x] - after[ay][ax])
                        count += 1
                if count:
                    best = min(best, total / count / 255.0)
    return 0.0 if not math.isfinite(best) else float(best)


def _render_room_pair_for_score(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    room: str,
) -> tuple[float | None, tuple[float, float, float, float] | None, tuple[float, float, float, float] | None]:
    try:
        before_crops = room_label_crops(before_path, before_page, room, max_crops=2)
        after_crops = room_label_crops(after_path, after_page, room, max_crops=2)
        if not before_crops or not after_crops:
            return None, None, None
        best: tuple[float, tuple[float, float, float, float], tuple[float, float, float, float]] | None = None
        # Try a small bounded cross-product because the same ROOM number can
        # occur in both a plan and an explication table.
        for before_clip in before_crops[:2]:
            for after_clip in after_crops[:2]:
                b_clip = _expand_clip(before_clip)
                a_clip = _expand_clip(after_clip)
                before_png = render_page_to_png_bytes(
                    before_path, before_page, max_dim=360, clip_frac=b_clip
                )
                after_png = render_page_to_png_bytes(
                    after_path, after_page, max_dim=360, clip_frac=a_clip
                )
                score = _aligned_grid_difference(before_png, after_png)
                if best is None or score > best[0]:
                    best = (score, b_clip, a_clip)
        if best is None:
            return None, None, None
        return best
    except Exception:  # noqa: BLE001
        return None, None, None


def prioritize_rooms(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_rooms: Sequence[str],
) -> tuple[list[str], list[dict]]:
    """Rank rooms by cheap local visual change while staying benchmark-blind."""
    selected = select_rooms(shared_rooms)
    scored: list[tuple[float, object, str]] = []
    diagnostics: list[dict] = []
    for room in selected:
        score, before_clip, after_clip = _render_room_pair_for_score(
            before_path, before_page, after_path, after_page, room
        )
        numeric_score = -1.0 if score is None else score
        scored.append((-numeric_score, room_sort_key(room), room))
        diagnostics.append({
            "room": room,
            "local_diff_score": None if score is None else round(score, 6),
            "before_clip": before_clip,
            "after_clip": after_clip,
        })
    scored.sort()
    ordered = [room for _, _, room in scored]
    rank = {room: index + 1 for index, room in enumerate(ordered)}
    for item in diagnostics:
        item["priority_rank"] = rank[item["room"]]
    diagnostics.sort(key=lambda item: item["priority_rank"])
    return ordered, diagnostics


def _fit_rect(width: float, height: float, box: pymupdf.Rect) -> pymupdf.Rect:
    scale = min(box.width / max(width, 1.0), box.height / max(height, 1.0))
    target_w = width * scale
    target_h = height * scale
    x0 = box.x0 + (box.width - target_w) / 2
    y0 = box.y0 + (box.height - target_h) / 2
    return pymupdf.Rect(x0, y0, x0 + target_w, y0 + target_h)


def make_paired_montage(
    before_rows: Sequence[tuple[str, bytes]],
    after_rows: Sequence[tuple[str, bytes]],
) -> bytes:
    """Build one large paired image; one ROOM per call is the default."""
    if not before_rows or not after_rows:
        return b""
    after_by_room = {room: png for room, png in after_rows}
    rows = [(room, png, after_by_room[room]) for room, png in before_rows if room in after_by_room]
    if not rows:
        return b""

    width = 1800.0
    header = 48.0
    row_height = 720.0 if len(rows) == 1 else 520.0
    gap = 18.0
    label_width = 120.0
    half_width = (width - label_width - gap * 3) / 2
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width, height=header + row_height * len(rows))
        page.insert_text((label_width + gap, 30), "PD", fontsize=16)
        page.insert_text((label_width + gap * 2 + half_width, 30), "RD / ID", fontsize=16)
        for index, (room, before_png, after_png) in enumerate(rows):
            y0 = header + index * row_height
            page.insert_text((12, y0 + 32), f"ROOM {room}", fontsize=14)
            left = pymupdf.Rect(
                label_width + gap, y0 + 8,
                label_width + gap + half_width, y0 + row_height - 8,
            )
            right = pymupdf.Rect(
                label_width + gap * 2 + half_width, y0 + 8,
                width - gap, y0 + row_height - 8,
            )
            before_pix = pymupdf.Pixmap(before_png)
            after_pix = pymupdf.Pixmap(after_png)
            page.insert_image(
                _fit_rect(float(before_pix.width), float(before_pix.height), left),
                stream=before_png,
            )
            page.insert_image(
                _fit_rect(float(after_pix.width), float(after_pix.height), right),
                stream=after_png,
            )
        return page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False).tobytes("png")
    finally:
        doc.close()


def grounded_rows(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    rooms: Sequence[str],
):
    before_rows: list[tuple[str, bytes]] = []
    after_rows: list[tuple[str, bytes]] = []
    usable: list[str] = []
    missing: list[str] = []
    selected_clips: dict[str, dict] = {}
    for room in rooms:
        score, before_clip, after_clip = _render_room_pair_for_score(
            before_path, before_page, after_path, after_page, room
        )
        if before_clip is None or after_clip is None:
            missing.append(room)
            continue
        try:
            before_png = render_page_to_png_bytes(
                before_path, before_page, max_dim=1450, clip_frac=before_clip
            )
            after_png = render_page_to_png_bytes(
                after_path, after_page, max_dim=1450, clip_frac=after_clip
            )
        except Exception:  # noqa: BLE001
            missing.append(room)
            continue
        before_rows.append((room, before_png))
        after_rows.append((room, after_png))
        usable.append(room)
        selected_clips[room] = {
            "local_diff_score": None if score is None else round(score, 6),
            "before_clip": before_clip,
            "after_clip": after_clip,
        }
    return before_rows, after_rows, usable, missing, selected_clips


def call_focused(before_rows, after_rows, config: LlmConfig) -> dict:
    montage = make_paired_montage(before_rows, after_rows)
    if not montage:
        return {}
    rooms = [room for room, _ in before_rows]
    room_text = ", ".join(rooms)
    digest = hashlib.sha256(montage + room_text.encode("utf-8")).hexdigest()
    user_text = (
        f"Обязательный список ROOM для проверки: {room_text}. "
        "Для каждого ROOM выполни полный порядок: читаемость -> инвентарь -> "
        "топология -> подключения -> параметры -> статус. Не ставь same, если "
        "не можешь уверенно закончить все эти проверки."
    )
    result = call_llm_json(
        config,
        _FOCUSED_ROOM_PROMPT,
        user_text,
        images=[png_bytes_to_data_url(montage)],
        operation="vision",
        source_digest=digest,
        prompt_version="focused-room-pair-v5-gigachat-review",
    )
    return result if isinstance(result, dict) else {}


def _looks_architectural_only(*parts: str) -> bool:
    text = " ".join(parts).casefold()
    if not any(phrase in text for phrase in _ARCHITECTURAL_PHRASES):
        return False
    return not any(stem in text for stem in _ENGINEERING_STEMS)


def _valid_bbox(value) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if any(number < 0.0 or number > 1.0 for number in numbers):
        return None
    if numbers[2] <= numbers[0] or numbers[3] <= numbers[1]:
        return None
    return numbers


def _normalized_differences(raw: dict) -> list[dict]:
    differences = raw.get("differences")
    if not isinstance(differences, list):
        legacy = str(raw.get("change") or "").strip()
        differences = [{"kind": "layout", "summary_ru": legacy}] if legacy else []
    accepted: list[dict] = []
    for item in differences:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary_ru") or item.get("change") or "").strip()
        if not summary:
            continue
        kind = str(item.get("kind") or "layout").strip().casefold()
        if kind not in _ALLOWED_KINDS:
            kind = "layout"
        accepted.append({
            "kind": kind,
            "summary_ru": summary,
            "bbox_pd": _valid_bbox(item.get("bbox_pd")),
            "bbox_rd": _valid_bbox(item.get("bbox_rd")),
        })
    return accepted


def normalize_checked_rooms(result: dict, usable_rooms: Sequence[str]) -> tuple[list[dict], list[str]]:
    """Validate model output; unreadable or incomplete checks never become same."""
    allowed = [normalize_room_key(room) for room in usable_rooms]
    allowed = [room for index, room in enumerate(allowed) if room and room not in allowed[:index]]
    allowed_set = set(allowed)
    best: dict[str, dict] = {}

    raw_rows = result.get("checked_rooms") if isinstance(result, dict) else []
    for raw in raw_rows if isinstance(raw_rows, list) else []:
        if not isinstance(raw, dict):
            continue
        room = normalize_room_key(raw.get("room", ""))
        status = str(raw.get("status") or "").strip().casefold()
        if room not in allowed_set or status not in _STATUS_PRIORITY:
            continue

        pd_observation = str(raw.get("pd_observation") or "").strip()
        rd_observation = str(raw.get("rd_observation") or "").strip()
        readability_pd = str(raw.get("readability_pd") or "").strip().casefold()
        readability_rd = str(raw.get("readability_rd") or "").strip().casefold()
        if readability_pd not in _READABILITY:
            readability_pd = "partial"
        if readability_rd not in _READABILITY:
            readability_rd = "partial"
        coverage_raw = raw.get("coverage") if isinstance(raw.get("coverage"), list) else []
        coverage = {
            str(item).strip().casefold()
            for item in coverage_raw
            if str(item).strip().casefold() in _REQUIRED_COVERAGE
        }
        differences = _normalized_differences(raw)

        if status == "changed" and not differences:
            status = "unclear"
        if status == "changed":
            summaries = " ".join(item["summary_ru"] for item in differences)
            if _looks_architectural_only(pd_observation, rd_observation, summaries):
                status = "unclear"
                differences = []
        if status == "same" and (
            readability_pd != "good"
            or readability_rd != "good"
            or coverage != _REQUIRED_COVERAGE
            or not pd_observation
            or not rd_observation
        ):
            status = "unclear"

        change = " | ".join(item["summary_ru"] for item in differences) if status == "changed" else ""
        item = {
            "room": room,
            "status": status,
            "readability_pd": readability_pd,
            "readability_rd": readability_rd,
            "coverage": sorted(coverage),
            "pd_observation": pd_observation,
            "rd_observation": rd_observation,
            "differences": differences if status == "changed" else [],
            "change": change,
        }
        previous = best.get(room)
        if previous is None or _STATUS_PRIORITY[status] > _STATUS_PRIORITY[previous["status"]]:
            best[room] = item

    checked = [best[room] for room in allowed if room in best]
    missing = [room for room in allowed if room not in best]
    return checked, missing


def compare_shared_rooms_focused(
    before_path: str,
    before_page: int,
    after_path: str,
    after_page: int,
    shared_rooms: Sequence[str],
    config: LlmConfig,
    *,
    max_calls: int,
) -> tuple[list[dict], list[dict], int]:
    if max_calls <= 0 or not shared_rooms:
        return [], [], 0

    ordered, priority = prioritize_rooms(
        before_path, before_page, after_path, after_page, shared_rooms
    )
    findings: list[dict] = []
    diagnostics: list[dict] = [{
        "status": "focused_room_priority",
        "room_priority": priority,
    }]
    calls_used = 0
    pair_budget = min(max_calls, MAX_FOCUSED_CALLS_PER_PAIR)

    for start in range(0, len(ordered), FOCUSED_ROOMS_PER_CALL):
        if calls_used >= pair_budget:
            break
        requested = ordered[start:start + FOCUSED_ROOMS_PER_CALL]
        before_rows, after_rows, usable, missing, selected_clips = grounded_rows(
            before_path, before_page, after_path, after_page, requested
        )
        if not usable:
            diagnostics.append({
                "status": "focused_no_grounded_crops",
                "requested_rooms": requested,
                "missing_rooms": missing,
            })
            continue

        calls_used += 1
        try:
            result = call_focused(before_rows, after_rows, config)
        except Exception as exc:  # noqa: BLE001
            diagnostics.append({
                "status": "focused_vision_error",
                "requested_rooms": requested,
                "usable_rooms": usable,
                "selected_clips": selected_clips,
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        checked, omitted = normalize_checked_rooms(result, usable)
        accepted: list[dict] = []
        for row in checked:
            if row["status"] != "changed":
                continue
            finding = {
                "label": "Инженерное визуальное различие",
                "change": row["change"],
                "rooms": [row["room"]],
                "pd_observation": row["pd_observation"],
                "rd_observation": row["rd_observation"],
                "differences": row["differences"],
            }
            accepted.append(finding)
            findings.append(finding)

        unclear_rooms = [row["room"] for row in checked if row["status"] == "unclear"] + omitted
        if accepted:
            status = "focused_significant"
        elif unclear_rooms:
            status = "focused_unclear"
        else:
            status = "focused_complete_no_change"

        diagnostics.append({
            "status": status,
            "requested_rooms": requested,
            "usable_rooms": usable,
            "missing_rooms": missing,
            "selected_clips": selected_clips,
            "omitted_by_model": omitted,
            "checked_rooms": checked,
            "findings": accepted,
            "summary": str(result.get("summary") or ""),
            "pair_call": calls_used,
            "pair_call_budget": pair_budget,
        })

    scheduled_capacity = pair_budget * FOCUSED_ROOMS_PER_CALL
    if len(ordered) > scheduled_capacity:
        diagnostics.append({
            "status": "focused_pair_budget",
            "rooms_total": len(ordered),
            "rooms_scheduled": min(len(ordered), scheduled_capacity),
            "calls_used": calls_used,
            "pair_call_budget": pair_budget,
        })

    return findings, diagnostics, calls_used
