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
import sys
from collections import Counter
from pathlib import Path

import pymupdf

from .anchors import normalize_room_key, normalize_room_references
from .llm import LlmConfig, call_llm_json
from .llm_runtime import parallel_map
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url

_REQUIREMENT_CHECK_TEMPLATE = f"""\
Ты помогаешь инспектору государственного строительного надзора проверить,
выполнено ли конкретное проектное решение в рабочей документации.

Тебе дано требование (проектное решение) из ПД и показан лист РД/ИД либо его
адресный фрагмент. Если листов два, ПЕРВЫЙ — источник требования из ПД
(схема или план), ВТОРОЙ — проверяемый лист РД/ИД. Делай вывод только из
показанного и из текста требования; исторические примеры и эталонные ответы
недоступны.

{UNTRUSTED_INPUT_RULE}

Сравнивай инженерную суть: наличие решения, состав и марки оборудования,
связи и подключения, трассы и ветки, параметры — а не рамку, масштаб,
оформление, мебель или качество рендера. Разный жанр листа (схема против
плана) сам по себе не является расхождением: сравнивай сущности и связи, а не
совпадение геометрии.

Вердикты:
  "confirmed" — требуемое решение явно присутствует;
  "absent" — показанное место явно видно НА ИНЖЕНЕРНОМ ЧЕРТЕЖЕ, но требуемого
             решения в нём нет;
  "unclear" — лист, масштаб или тип изображения не позволяют решить вопрос.

Отдельно оцени `comparability` — можно ли ВООБЩЕ судить по показанному:
  "high" — видна инженерная графика нужного места, читается;
  "medium" — видна частично, мелко или перекрыто;
  "low" — показана только таблица, экспликация, штамп или пустая область.

Очень важно: экспликация помещений, таблица, штамп или подпись номера
помещения НЕ доказывают отсутствие инженерного решения. Если показано только
это — comparability="low" и verdict="unclear". Отсутствие СЛОВА не является
доказательством отсутствия графического элемента.

Если лист плотный и решение нужно рассмотреть ближе, предложи зоны для
увеличения в `candidate_regions`: координаты долями листа от левого верхнего
угла, [x1,y1,x2,y2], каждое число от 0 до 1. Предлагай зоны и тогда, когда
ответить уверенно не получилось, — это способ попросить увеличение, а не
признание неудачи. Если увеличение не нужно, верни пустой список.

Отвечай только JSON:
{{"verdict":"confirmed|absent|unclear",
 "comparability":"high|medium|low",
 "reason":"что именно видно на листе",
 "where":"номер помещения, оси или другая координатная привязка",
 "candidate_regions":[{{"reason":"что там рассмотреть",
                       "rd_bbox_norm":[0.0,0.0,1.0,1.0],
                       "priority":"high|medium|low"}}]}}"""


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


_COMPARABILITY = ("high", "medium", "low")

# Соответствие вердикта и статуса требования. Статус — то, что видит
# инспектор; вердикт остаётся машинным полем ступени.
_REQUIREMENT_STATUS = {
    "confirmed": "appears_compliant",
    "absent": "candidate_difference",
    "unclear": "unclear",
}


def normalize_regions(value: object, limit: int = 4) -> list[dict]:
    """Зоны увеличения, предложенные моделью, — только разбираемые.

    Координаты приходят от модели, то есть это недоверенные данные: рамка с
    перепутанными углами, выходящая за лист или схлопнутая в точку, молча
    дала бы пустой или неверный кроп. Такая зона отбрасывается целиком, а не
    чинится догадкой.
    """
    out: list[dict] = []
    for item in (value or []) if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        raw = item.get("rd_bbox_norm") or item.get("bbox_norm")
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in raw)
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
        y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
        if x2 <= x1 or y2 <= y1:
            continue
        priority = str(item.get("priority") or "").strip().casefold()
        out.append({
            "reason": str(item.get("reason") or "").strip(),
            "rd_bbox_norm": (x1, y1, x2, y2),
            "priority": priority if priority in ("high", "medium", "low") else "medium",
        })
        if len(out) >= limit:
            break
    return out


def _normalized_answer(result: dict) -> dict:
    """Ответ модели, приведённый к контракту ступени.

    Главное правило здесь — fail-closed в сторону «не подтверждено»:
    «выполнено» при низкой читаемости листа означает не выполнение, а то,
    что судить было не по чему. Обратное направление не трогаем: понижать
    «не найдено» до «не разобрать» модель вправе сама, а вот повышать
    сомнительное до подтверждения нельзя (Г.10, раздел 13 задания).
    """
    comparability = str(result.get("comparability") or "").strip().casefold()
    if comparability not in _COMPARABILITY:
        comparability = "medium"
    verdict = result.get("verdict")
    downgraded = ""
    if verdict == "confirmed" and comparability == "low":
        verdict = "unclear"
        downgraded = (" Понижено до «не разобрать»: подтверждение при низкой "
                      "читаемости листа не является подтверждением.")
    return {
        **result,
        "verdict": verdict,
        "comparability": comparability,
        "requirement_status": _REQUIREMENT_STATUS[verdict],
        "reason": str(result.get("reason") or "") + downgraded,
        "candidate_regions": normalize_regions(result.get("candidate_regions")),
    }


def check_requirement_on_page(
    rd_pdf_path: str,
    rd_page_no: int,
    requirement_text: str,
    rooms: list[str],
    config: LlmConfig,
    discipline: str | None = None,
    timeout: float = 120.0,
    clip_frac: tuple[float, float, float, float] | None = None,
    pd_images: list[str] | None = None,
) -> dict:
    """Один вызов «требование + лист РД -> статус» на оба вида источника.

    Требование может быть извлечено из текста ПД или показано на листе ПД —
    это меняет только то, какие изображения приложены к вызову, а не сам
    вопрос к модели и не разбор ответа. Отдельного конвейера на
    «текст -> чертёж» и на «чертёж -> чертёж» не нужно: они отличаются
    вложением, а не смыслом (раздел 14 задания).
    """
    room_keys = normalize_room_references(rooms)
    rooms_str = ", ".join(room_keys) if room_keys else "не указаны"
    crop_note = (
        " Показан адресный фрагмент листа; если на нём только таблица или "
        "экспликация, comparability=low и verdict=unclear."
        if clip_frac is not None else ""
    )
    source_note = (
        " Первое изображение — лист ПД, источник требования; второе — "
        "проверяемый лист РД/ИД."
        if pd_images else ""
    )
    user_text = (
        "Требование из ПД:\n"
        f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n{requirement_text}\n</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"Проверяемая зона: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{rooms_str}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>."
        f"{source_note}{crop_note}"
    )
    try:
        image = render_page_to_data_url(rd_pdf_path, rd_page_no, clip_frac=clip_frac)
        images = [*(pd_images or []), image]
        image_digest = hashlib.sha256("".join(images).encode("utf-8")).hexdigest()
        source_digest = hashlib.sha256(
            f"{image_digest}:{requirement_text}:{rooms_str}:{clip_frac}".encode("utf-8")
        ).hexdigest()
        result = call_llm_json(
            config,
            requirement_check_system_prompt(discipline),
            user_text,
            images=images,
            timeout=timeout,
            operation="vision",
            source_digest=source_digest,
            prompt_version="requirement-page-v5-unified",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "unclear",
            "comparability": "low",
            "requirement_status": "unclear",
            "reason": f"ОШИБКА: {type(exc).__name__}: {exc}",
            "where": "",
            "candidate_regions": [],
            "error": True,
        }
    if not isinstance(result, dict) or result.get("verdict") not in {
        "confirmed", "absent", "unclear"
    }:
        return {
            "verdict": "unclear",
            "comparability": "low",
            "requirement_status": "unclear",
            "reason": "ИИ не дал разбираемый ответ",
            "where": "",
            "candidate_regions": [],
            "error": True,
        }
    return _normalized_answer(result)


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


def rd_page_pool(sources: list[tuple]) -> list[dict]:
    """Все листы рабочей документации как кандидаты — по одному разу на прогон.

    Нужен там, где требование ПД не названо номером помещения: без него
    реестр помещений подобрать лист не может, и раньше просмотр не
    выполнялся вовсе. Но «не за что зацепиться реестром» — это не «смотреть
    нечего»: лист выбирается по тексту самого требования, а когда текста на
    листе нет (подписи чертежа в кривых, Г.8), лист остаётся кандидатом с
    нулевым весом, а не исчезает — иначе именно графика, ради которой
    проверка и нужна, выпадала бы первой (Г.10).

    Пул строится один раз: перебор страниц дешёвый, но на каждое требование
    он повторялся бы десятки раз.
    """
    pool: list[dict] = []
    for source in sources:
        path, display_name = str(source[0]), str(source[1])
        file = Path(path)
        if not file.is_file():
            continue
        try:
            doc = pymupdf.open(path)
        except Exception as exc:  # noqa: BLE001 — битый файл не роняет подбор
            print(f"лист не подобран ({exc}): {display_name}", file=sys.stderr)
            continue
        try:
            for index in range(doc.page_count):
                pool.append({
                    "path": path,
                    "page": index + 1,
                    "text": doc[index].get_text("text").strip(),
                    "name": display_name,
                })
        finally:
            doc.close()
    return pool


def rank_pool_for_requirement(pool: list[dict], sentence: str, max_pages: int) -> list[dict]:
    """Листы пула в порядке близости к тексту требования, без отсева.

    Вес — та же редкость общих слов, что и при подборе по помещению
    (`select_candidate_pages`): одно правило ранжирования на оба пути, а не
    два расходящихся. Порядок при равенстве — по номеру листа, поэтому
    кандидаты чередуются между файлами, а не выбираются все из первого
    (Г.52).
    """
    if max_pages <= 0 or not pool:
        return []
    words = [set(re.findall(r"\w+", str(entry.get("text") or "").casefold()))
             for entry in pool]
    frequencies = Counter(word for tokens in words for word in tokens)
    query = set(re.findall(r"\w+", str(sentence or "").casefold()))
    scored = [
        (sum(1 / frequencies[word] for word in (tokens & query) if frequencies[word]),
         entry)
        for tokens, entry in zip(words, pool)
    ]
    ordered = sorted(
        range(len(scored)),
        key=lambda i: (-scored[i][0], int(scored[i][1]["page"]), str(scored[i][1]["name"])),
    )
    return [scored[i][1] for i in ordered[:max_pages]]


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
