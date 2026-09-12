"""Generic room-focused fallback for blind drawing comparison.

The first vision pass has one job only: state observable PD -> RD differences.
It deliberately does not ask for legal qualification, severity or field actions.
Every shown room must be accounted for as changed, same or unclear so an empty
list can never masquerade as a completed inspection.
"""
from __future__ import annotations

import hashlib
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


MAX_FOCUSED_ROOM_CALLS = _positive_int_env("NADZOR_MAX_FOCUSED_ROOM_VISION_CALLS", 12, 0, 40)
FOCUSED_ROOMS_PER_CALL = _positive_int_env("NADZOR_FOCUSED_ROOMS_PER_CALL", 4, 1, 8)
MAX_FOCUSED_ROOMS_PER_PAIR = _positive_int_env("NADZOR_MAX_FOCUSED_ROOMS_PER_PAIR", 12, 1, 32)

_ROOM_NUM_RE = re.compile(r"^(\d{1,4})(?:\.(\d+))?([а-яё]?)$", re.IGNORECASE)
_STATUS_PRIORITY = {"same": 0, "unclear": 1, "changed": 2}

_FOCUSED_ROOM_PROMPT = f"""\
Ты сравниваешь инженерные чертежи ПД и РД/ИД. На этом этапе нужна только
фиксация ВИДИМЫХ различий. Не делай юридических выводов, не оценивай severity,
не предлагай проверку на объекте и не решай, является ли различие нарушением.

На первом изображении — монтаж фрагментов ПД, на втором — монтаж фрагментов
РД/ИД. Строки подписаны ROOM <номер>. Сравнивай только ROOM с тем же номером.
Не сравнивай соседние строки между собой.

Для КАЖДОГО переданного ROOM обязан вернуть ровно один результат:
- changed — видишь конкретное инженерное отличие между ПД и РД/ИД;
- same — обе стороны достаточно читаемы и соответствующее решение выглядит одинаковым;
- unclear — хотя бы одна сторона нечитабельна, показана только таблица/экспликация,
  зона обрезана или нельзя уверенно сопоставить инженерное решение.

Проверяй прежде всего наличие/отсутствие инженерных элементов, состав и
количество оборудования, подключение, трассу, конфигурацию и положение
элементов. Не считай различием рамку, штамп, масштаб, цвет, шрифт, качество
рендера или компоновку листа. Не делай вывод об отсутствии элемента только
из отсутствия слова в тексте.

Если ставишь changed, напиши по одному короткому наблюдению для ПД и РД и
одно конкретное предложение change. Если доказательства недостаточно — unclear,
а не same. Нельзя пропускать ROOM и нельзя придумывать номера вне списка.

{UNTRUSTED_INPUT_RULE}

Отвечай только JSON:
{{"checked_rooms":[
  {{"room":"101","status":"changed|same|unclear",
    "pd_observation":"что видно в ПД",
    "rd_observation":"что видно в РД/ИД",
    "change":"конкретное различие; пусто для same/unclear"}}
],
"summary":"кратко, без правовой оценки"}}
"""


def room_sort_key(room: str):
    key = normalize_room_key(room)
    match = _ROOM_NUM_RE.fullmatch(key)
    if not match:
        return (1, key)
    return (0, int(match.group(1)), int(match.group(2) or 0), (match.group(3) or "").casefold())


def select_rooms(shared_rooms: Sequence[str]) -> list[str]:
    rooms = {normalize_room_key(room) for room in shared_rooms if normalize_room_key(room)}
    return sorted(rooms, key=room_sort_key)[:MAX_FOCUSED_ROOMS_PER_PAIR]


def _fit_rect(width: float, height: float, box: pymupdf.Rect) -> pymupdf.Rect:
    scale = min(box.width / max(width, 1.0), box.height / max(height, 1.0))
    target_w = width * scale
    target_h = height * scale
    x0 = box.x0 + (box.width - target_w) / 2
    y0 = box.y0 + (box.height - target_h) / 2
    return pymupdf.Rect(x0, y0, x0 + target_w, y0 + target_h)


def make_montage(rows: Sequence[tuple[str, bytes]]) -> bytes:
    if not rows:
        return b""
    width = 900.0
    row_height = 330.0
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width, height=row_height * len(rows))
        for index, (room, png) in enumerate(rows):
            y = index * row_height
            page.insert_text((16, y + 22), f"ROOM {room}", fontsize=13)
            pix = pymupdf.Pixmap(png)
            box = pymupdf.Rect(16, y + 32, width - 16, y + row_height - 10)
            page.insert_image(_fit_rect(float(pix.width), float(pix.height), box), stream=png)
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
    for room in rooms:
        before_crops = room_label_crops(before_path, before_page, room, max_crops=3)
        after_crops = room_label_crops(after_path, after_page, room, max_crops=3)
        if not before_crops or not after_crops:
            missing.append(room)
            continue
        try:
            before_png = render_page_to_png_bytes(
                before_path, before_page, max_dim=900, clip_frac=before_crops[0]
            )
            after_png = render_page_to_png_bytes(
                after_path, after_page, max_dim=900, clip_frac=after_crops[0]
            )
        except Exception:
            missing.append(room)
            continue
        before_rows.append((room, before_png))
        after_rows.append((room, after_png))
        usable.append(room)
    return before_rows, after_rows, usable, missing


def call_focused(before_rows, after_rows, config: LlmConfig) -> dict:
    before_png = make_montage(before_rows)
    after_png = make_montage(after_rows)
    if not before_png or not after_png:
        return {}
    rooms = [room for room, _ in before_rows]
    room_text = ", ".join(rooms)
    digest = hashlib.sha256(before_png + b"\0" + after_png + room_text.encode("utf-8")).hexdigest()
    user_text = (
        "Первое изображение — ПД, второе — РД/ИД. "
        f"Обязательный список для проверки: {room_text}. "
        "Верни checked_rooms ровно по этим ROOM; ни один не пропускай."
    )
    result = call_llm_json(
        config,
        _FOCUSED_ROOM_PROMPT,
        user_text,
        images=[png_bytes_to_data_url(before_png), png_bytes_to_data_url(after_png)],
        operation="vision",
        source_digest=digest,
        prompt_version="focused-room-pair-v3",
    )
    return result if isinstance(result, dict) else {}


def normalize_checked_rooms(result: dict, usable_rooms: Sequence[str]) -> tuple[list[dict], list[str]]:
    """Validate the model contract without turning omissions into negative evidence."""
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
        change = str(raw.get("change") or "").strip()
        if status == "changed" and not change:
            status = "unclear"
        if status == "same" and (not pd_observation or not rd_observation):
            status = "unclear"
        item = {
            "room": room,
            "status": status,
            "pd_observation": pd_observation,
            "rd_observation": rd_observation,
            "change": change if status == "changed" else "",
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
    ordered = select_rooms(shared_rooms)
    findings: list[dict] = []
    diagnostics: list[dict] = []
    calls_used = 0

    for start in range(0, len(ordered), FOCUSED_ROOMS_PER_CALL):
        if calls_used >= max_calls:
            break
        requested = ordered[start:start + FOCUSED_ROOMS_PER_CALL]
        before_rows, after_rows, usable, missing = grounded_rows(
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
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        checked, omitted = normalize_checked_rooms(result, usable)
        accepted: list[dict] = []
        for row in checked:
            if row["status"] != "changed":
                continue
            finding = {
                "label": "Визуальное различие",
                "change": row["change"],
                "rooms": [row["room"]],
                "pd_observation": row["pd_observation"],
                "rd_observation": row["rd_observation"],
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
            "omitted_by_model": omitted,
            "checked_rooms": checked,
            "findings": accepted,
            "summary": str(result.get("summary") or ""),
        })

    return findings, diagnostics, calls_used
