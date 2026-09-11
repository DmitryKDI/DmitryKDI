"""Targeted visual verification of project requirements on RD/ID sheets.

The mechanism is benchmark-blind.  Requirements are grounded by document
room anchors, checked room-by-room, and an ``unclear`` whole-page verdict may
be retried on small crops around the exact room label.  The crop retry is a
recall mechanism, not a shortcut to ``absent``: a crop containing only an
explication/table must remain ``unclear``.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter

import pymupdf

from .anchors import normalize_room_key, normalize_room_references
from .llm import LlmConfig, call_llm_json
from .llm_runtime import parallel_map
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url

_REQUIREMENT_CHECK_TEMPLATE = f"""\
Ты помогаешь инспектору государственного строительного надзора проверить,
выполнено ли конкретное требование проектной документации на листе РД/ИД.

Тебе показан один лист РД/ИД или адресный фрагмент этого листа. Отдельно дано
требование из ПД и одна конкретная зона/помещение. Делай вывод только из
показанного изображения и текста требования; исторические примеры и эталонные
ответы недоступны.

{UNTRUSTED_INPUT_RULE}

Вердикты:
  "confirmed" — требуемое решение явно присутствует в указанной зоне;
  "absent" — указанная зона явно видна НА ИНЖЕНЕРНОМ ЧЕРТЕЖЕ, но требуемого
             решения в ней нет;
  "unclear" — лист/масштаб/тип схемы не позволяют уверенно решить вопрос.

Очень важно: экспликация помещений, таблица, штамп или просто подпись номера
помещения НЕ доказывают отсутствие инженерного решения. Если показанный
фрагмент содержит только таблицу/экспликацию и не показывает инженерную
графику помещения, верни "unclear". Не считай отсутствие слова доказательством
отсутствия графического элемента.

Отвечай только JSON:
{{"verdict":"confirmed|absent|unclear",
 "reason":"что именно видно на листе",
 "where":"номер помещения, оси или другая координатная привязка"}}"""


def _positive_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


MAX_VISUAL_ROOM_CHECKS = _positive_int_env("NADZOR_MAX_VISUAL_ROOM_CHECKS", 24, 1, 100)
MAX_ROOM_CROP_RETRIES = _positive_int_env("NADZOR_MAX_ROOM_CROP_RETRIES", 2, 0, 4)
_ROOM_KEY_RE = re.compile(r"^\d{1,4}(?:\.\d+)?[а-яё]?$", re.IGNORECASE)


def requirement_check_system_prompt(discipline: str | None = None) -> str:
    del discipline
    return _REQUIREMENT_CHECK_TEMPLATE


def check_requirement_on_page(
    rd_pdf_path: str,
    rd_page_no: int,
    requirement_text: str,
    rooms: list[str],
    config: LlmConfig,
    discipline: str | None = None,
    timeout: float = 120.0,
    clip_frac: tuple[float, float, float, float] | None = None,
) -> dict:
    room_keys = normalize_room_references(rooms)
    rooms_str = ", ".join(room_keys) if room_keys else "не указаны"
    crop_note = (
        " Показан адресный фрагмент вокруг найденной подписи помещения; "
        "если это только экспликация/таблица, верни unclear."
        if clip_frac is not None else ""
    )
    user_text = (
        "Требование из ПД:\n"
        f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n{requirement_text}\n</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"Проверяемая зона: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{rooms_str}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>."
        f"{crop_note}"
    )
    try:
        image = render_page_to_data_url(rd_pdf_path, rd_page_no, clip_frac=clip_frac)
        image_digest = hashlib.sha256(image.encode("utf-8")).hexdigest()
        source_digest = hashlib.sha256(
            f"{image_digest}:{requirement_text}:{rooms_str}:{clip_frac}".encode("utf-8")
        ).hexdigest()
        result = call_llm_json(
            config,
            requirement_check_system_prompt(discipline),
            user_text,
            images=[image],
            timeout=timeout,
            operation="vision",
            source_digest=source_digest,
            prompt_version="requirement-page-v4",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "unclear",
            "reason": f"ОШИБКА: {type(exc).__name__}: {exc}",
            "where": "",
            "error": True,
        }
    if not isinstance(result, dict) or result.get("verdict") not in {
        "confirmed", "absent", "unclear"
    }:
        return {
            "verdict": "unclear",
            "reason": "ИИ не дал разбираемый ответ",
            "where": "",
            "error": True,
        }
    return result


def _canonical_room_index(room_index: dict[str, list[dict]]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for raw_key, entries in room_index.items():
        key = normalize_room_key(raw_key)
        if key:
            out.setdefault(key, []).extend(entries or [])
    return out


def select_candidate_pages(
    rooms: list[str],
    room_index: dict[str, list[dict]],
    max_pages: int,
    sentence: str = "",
) -> list[dict]:
    """Rank pages by exact room anchor, then by rare words from requirement."""
    keys = normalize_room_references(rooms)
    canonical_index = _canonical_room_index(room_index)
    lists = [canonical_index.get(key, []) for key in keys]

    entries: dict[tuple[str, int], dict] = {}
    for group in lists:
        for entry in group:
            key = (str(entry["path"]), int(entry["page"]))
            if key not in entries or not entry.get("level_fallback"):
                entries[key] = entry
    if not entries:
        return []

    words = {
        key: set(re.findall(r"\w+", str(entry.get("text") or "").casefold()))
        for key, entry in entries.items()
    }
    frequencies = Counter(word for tokens in words.values() for word in tokens)
    query = set(re.findall(r"\w+", sentence.casefold()))
    scores = {
        key: sum(1 / frequencies[word] for word in (tokens & query) if frequencies[word])
        for key, tokens in words.items()
    }
    ordered = sorted(
        entries,
        key=lambda key: (
            bool(entries[key].get("level_fallback")),
            -scores[key],
            str(entries[key].get("name") or ""),
            key[1],
        ),
    )
    return [entries[key] for key in ordered[:max_pages]]


def _candidate_pages(rooms: list[str], room_index: dict[str, list[dict]], max_pages: int) -> list[dict]:
    return select_candidate_pages(rooms, room_index, max_pages)


def _room_tasks(findings: list) -> list[tuple[object, str | None]]:
    tasks: list[tuple[object, str | None]] = []
    for finding in findings:
        if getattr(finding, "finding_type", None) != "no_code_visual_check_needed":
            continue
        rooms = normalize_room_references(getattr(finding, "rooms", []) or [])
        if rooms:
            tasks.extend((finding, room) for room in rooms)
        else:
            tasks.append((finding, None))
        if len(tasks) >= MAX_VISUAL_ROOM_CHECKS:
            break
    return tasks[:MAX_VISUAL_ROOM_CHECKS]


def _rect_to_fraction(page_rect, rect) -> tuple[float, float, float, float]:
    return (
        max(0.0, rect.x0 / page_rect.width),
        max(0.0, rect.y0 / page_rect.height),
        min(1.0, rect.x1 / page_rect.width),
        min(1.0, rect.y1 / page_rect.height),
    )


def room_label_crops(
    pdf_path: str,
    page_no: int,
    room: str,
    *,
    max_crops: int = MAX_ROOM_CROP_RETRIES,
) -> list[tuple[float, float, float, float]]:
    """Find bounded crops around exact room labels using the PDF text layer.

    Several occurrences are intentionally allowed: the same number can be in
    an explication and on the plan.  Vision is explicitly instructed to reject
    table-only crops, so we preserve both possibilities instead of guessing
    which occurrence is the real room polygon.
    """
    key = normalize_room_key(room)
    if max_crops <= 0 or not key or not _ROOM_KEY_RE.fullmatch(key):
        return []
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:  # noqa: BLE001
        return []
    try:
        if page_no < 1 or page_no > doc.page_count:
            return []
        page = doc[page_no - 1]
        hits = page.search_for(key)
        if not hits:
            return []
        page_rect = page.rect
        crops: list[tuple[float, float, float, float]] = []
        seen: set[tuple[int, int]] = set()
        # Prefer labels away from page borders/title block, then spread across
        # the page so table and plan occurrences can both be inspected.
        ranked = sorted(
            hits,
            key=lambda rect: (
                rect.y0 > page_rect.height * 0.82,
                rect.x0 > page_rect.width * 0.86,
                rect.y0,
                rect.x0,
            ),
        )
        for hit in ranked:
            cx = (hit.x0 + hit.x1) / 2
            cy = (hit.y0 + hit.y1) / 2
            bucket = (int(cx / max(1.0, page_rect.width) * 8), int(cy / max(1.0, page_rect.height) * 8))
            if bucket in seen:
                continue
            seen.add(bucket)
            margin_x = max(page_rect.width * 0.12, hit.width * 10)
            margin_y = max(page_rect.height * 0.10, hit.height * 14)
            clip = pymupdf.Rect(
                max(page_rect.x0, hit.x0 - margin_x),
                max(page_rect.y0, hit.y0 - margin_y),
                min(page_rect.x1, hit.x1 + margin_x),
                min(page_rect.y1, hit.y1 + margin_y),
            )
            crops.append(_rect_to_fraction(page_rect, clip))
            if len(crops) >= max_crops:
                break
        return crops
    finally:
        doc.close()


def check_visual_candidates(
    findings: list,
    room_index: dict[str, list[dict]],
    config: LlmConfig,
    discipline: str | None = None,
    max_pages_per_finding: int = 3,
    on_result=None,
) -> list[dict]:
    """Check each explicit room anchor independently, with grounded crop retry."""
    tasks = _room_tasks(findings)

    def _check_task(task):
        finding, room = task
        sentence = str(getattr(finding, "sentence_pd", "") or "")
        if room is None:
            return {
                "rooms": [],
                "sentence": sentence,
                "verdict": "unclear",
                "reason": "у требования нет конкретного помещения/зоны для адресной проверки",
                "where": "",
                "pages_checked": 0,
                "crops_checked": 0,
            }

        pages = select_candidate_pages([room], room_index, max_pages_per_finding, sentence)
        if not pages:
            return {
                "rooms": [room],
                "sentence": sentence,
                "verdict": "unclear",
                "reason": "помещение не найдено в реестре РД — нет обоснованного листа для vision-проверки",
                "where": room,
                "pages_checked": 0,
                "crops_checked": 0,
            }

        checked = 0
        crops_checked = 0
        last_reason = ""
        last_where = room
        for entry in pages:
            checked += 1
            result = check_requirement_on_page(
                entry["path"], int(entry["page"]), sentence, [room], config, discipline
            )
            verdict = result.get("verdict")
            last_reason = str(result.get("reason") or last_reason)
            last_where = str(result.get("where") or last_where)
            if verdict in {"confirmed", "absent"}:
                return {
                    "rooms": [room],
                    "sentence": sentence,
                    "verdict": verdict,
                    "reason": last_reason,
                    "where": last_where,
                    "pages_checked": checked,
                    "crops_checked": crops_checked,
                    "page": int(entry["page"]),
                    "document": str(entry.get("name") or ""),
                }

            # Whole-page uncertainty often comes from unreadably small labels.
            # Retry only around exact labels found in this same grounded page.
            for crop in room_label_crops(
                str(entry["path"]), int(entry["page"]), room,
                max_crops=MAX_ROOM_CROP_RETRIES,
            ):
                crops_checked += 1
                focused = check_requirement_on_page(
                    entry["path"], int(entry["page"]), sentence, [room], config,
                    discipline, clip_frac=crop,
                )
                focused_verdict = focused.get("verdict")
                last_reason = str(focused.get("reason") or last_reason)
                last_where = str(focused.get("where") or last_where)
                if focused_verdict in {"confirmed", "absent"}:
                    return {
                        "rooms": [room],
                        "sentence": sentence,
                        "verdict": focused_verdict,
                        "reason": last_reason,
                        "where": last_where,
                        "pages_checked": checked,
                        "crops_checked": crops_checked,
                        "page": int(entry["page"]),
                        "document": str(entry.get("name") or ""),
                        "focused": True,
                    }
        return {
            "rooms": [room],
            "sentence": sentence,
            "verdict": "unclear",
            "reason": last_reason or "проверенные листы не позволяют сделать уверенный вывод",
            "where": last_where,
            "pages_checked": checked,
            "crops_checked": crops_checked,
        }

    out: list[dict] = []
    for result in parallel_map(_check_task, tasks):
        out.append(result)
        if on_result:
            on_result(result)
    return out


def render_vision_finding_line(result: dict) -> str:
    rooms_str = ", ".join(result.get("rooms") or []) or "без привязки"
    where = f" [{result['where']}]" if result.get("where") else ""
    return f"[{result.get('verdict', 'unclear')}] помещения {rooms_str}: {result.get('reason', '')}{where}"


def render_vision_requirement_report(results: list[dict]) -> str:
    lines = [
        "=== Адресная vision-проверка требований ПД по листам РД/ИД ===",
        f"Проверок: {len(results)}",
    ]
    for verdict in ("absent", "confirmed", "unclear"):
        group = [item for item in results if item.get("verdict") == verdict]
        if not group:
            continue
        lines.append(f"\n--- {verdict} ({len(group)}) ---")
        lines.extend(f"  {render_vision_finding_line(item)}" for item in group)
    return "\n".join(lines)
