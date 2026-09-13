"""Сверка «выполнено ли в РД то, что требует ПД» (Г.96).

Указание пользователя вместе с его предупреждением: «имей в виду, что может
быть много ложных срабатываний, ведь в РД меньше текста. Тут мы будем
смотреть по списку требований ПД и то, что в РД написано. Например, если в
ПД указано, что в таких-то местах делаем так, то система уже в РД смотрит
по чертежам (если нет требования в тексте), так ли сделано».

## Главное правило модуля

**Отсутствие подтверждения НИКОГДА не называется нарушением.** В рабочей
документации связного текста почти нет по природе: подписи чертежей
переведены в кривые (Г.8, измерено в Г.44/Г.59), а требование, которое в ПД
написано словами, в РД выражено линией на плане и позицией в спецификации.
Поэтому «не нашли в тексте РД» — ожидаемое состояние, а не улика. Инспектор
получает три статуса, и ни один из них не вердикт:

  подтверждено      — нашли прямое совпадение (текстом или на листе);
  требует проверки  — не подтвердили; названы листы, куда смотреть;
  не проверялось    — проверить было нечем (нет ключа, сбой, не за что
                      зацепиться визуально).

Это прямое следствие Б.6: система формирует гипотезы, а не заключения.

## Лестница проверки — от дешёвого к дорогому

1. Совпадение обозначения само по себе не подтверждает требование:
   параметры, место применения и условия проверяются по смыслу.
2. **Смысловая сверка текста РД моделью** (`requirement_text_verify`, Г.49):
   пачками, вызов на пачку со списком ещё нерешённых требований, а не на
   каждое требование отдельно.
3. **Зрение по листам РД** (`vision_page_compare`, Г.35) — только для
   требований, привязанных к номерам помещений: иначе не за что зацепиться,
   и лист выбирать не по чему. Требование к объекту целиком честно
   помечается «визуально проверять не по чему», а не гоняется вслепую.

Каждая следующая ступень работает ТОЛЬКО над тем, что не решила
предыдущая.
"""
from __future__ import annotations

import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

from .vision_page_compare import (
    rank_pool_for_requirement,
    rd_page_pool,
    select_candidate_pages,
)

from .llm import LlmConfig
from .requirement_text_verify import validated_verdict_evidence
from .requirement_registry import Requirement

STATUS_CONFIRMED = "подтверждено"
STATUS_NEEDS_CHECK = "требует проверки"
STATUS_NOT_CHECKED = "не проверялось"

# Требование без номера помещения: лист выбран по тексту требования, а не
# по реестру помещений. Инспектор должен видеть, по чему ему подобрали
# лист, — это догадка другого рода, чем прямое совпадение номера (Г.106).
_TEXT_PICK_NOTE = (" (требование не привязано к номеру помещения; листы подобраны по близости текста листа к тексту требования)")

# Сколько листов РД смотреть зрением на одно требование. Держится малым
# намеренно: зрение — самая дорогая ступень (~6 с на лист, Г.30), а цель
# здесь не доказать выполнение, а показать инспектору, куда смотреть.
DEFAULT_MAX_VISUAL_PAGES = 3

# Сколько увеличений зон, предложенных моделью, разрешено на одно
# требование. Бюджет просмотра, а не граница истины: увеличение стоит
# вызова, и без потолка одно требование выбрало бы весь лимит.
MAX_REQUIREMENT_ZOOMS = int(os.environ.get("NADZOR_MAX_REQUIREMENT_ZOOMS", "2"))


@dataclass
class ComplianceItem:
    requirement: Requirement
    status: str
    detail: str
    evidence: str = ""
    # Листы РД, на которых стоит посмотреть глазами: [(имя файла, страница)]
    pages_to_check: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class ComplianceResult:
    items: list[ComplianceItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # Что в этом прогоне не выполнялось и почему — видимое состояние (Г.10).
    not_run: list[str] = field(default_factory=list)
    # Путь файла -> имя тома. В отчёте инспектору нужно имя: путь вида
    # `uploads/632379a0e5….pdf` не открыть и не найти (Г.117).
    display_names: dict[str, str] = field(default_factory=dict)
    # Диагностика отдельно от пользовательских статусов и HTTP-схемы.
    diagnostics: dict[str, int | float] = field(default_factory=dict)


def check_compliance(
    requirements: list[Requirement],
    rd_text_facts: list[dict],
    rd_sources: list[tuple[str, str]],
    config: LlmConfig | None,
    llm_verify: Callable | None = None,
    vision_check: Callable | None = None,
    candidate_pages: Callable | None = None,
    max_visual_pages: int = DEFAULT_MAX_VISUAL_PAGES,
    on_progress: Callable[[int, int, str], None] | None = None,
    display_names: dict[str, str] | None = None,
    *,
    room_index: dict[str, list[dict]] | None = None,
) -> ComplianceResult:
    """Прогоняет список требований ПД по лестнице проверок против РД.

    `llm_verify`, `vision_check`, `candidate_pages` внедряются параметрами,
    а не берутся из модуля: так лестницу можно проверить тестом без сети и
    без реальных PDF, и так же видно, что модуль ничего не вызывает сам,
    когда ключа нет.
    """
    result = ComplianceResult(display_names=dict(display_names or {}))
    result.diagnostics = dict.fromkeys((
        "candidates_available", "candidates_selected", "unique_candidate_pages",
        "vision_calls", "unique_vision_pages", "repeated_vision_pairs",
        "vision_errors", "vision_unclear", "vision_seconds", "zoom_calls"), 0)
    candidate_keys = set()
    vision_keys = set()
    vision_pairs = set()

    # Ход работы наружу: сколько требований уже получили ответ из скольких.
    # Через него же вызывающий останавливает прогон — исключением из
    # обработчика (Г.114). Поэтому он вызывается ВНУТРИ ступеней, а не
    # только между ними: между ступенями остановка ждала бы конца самой
    # долгой из них, то есть не работала бы там, где нужна.
    total = len(requirements)

    def _report(note: str) -> None:
        """Ход работы вместе с НАЗВАНИЕМ ступени.

        Г.116 — без названия ступени счётчик замирает на всё время смысловой
        сверки (она идёт одним вызовом на весь список), и прогон выглядит
        зависшим. Ступени идут с разной скоростью, и человек должен видеть,
        какая из них сейчас, а не только число.
        """
        if on_progress is not None:
            on_progress(len(result.items), total, note)

    # Обозначение может встретиться у другого элемента или в другом месте.
    # Поэтому все требования проходят содержательную проверку.
    pending = list(requirements)
    if not pending:
        return _finish(result)

    # Пул листов РД на случай требования без номера помещения. Лениво и один
    # раз: перебор страниц дешёвый, но на каждое требование повторялся бы
    # десятки раз.
    page_pool: list[dict] | None = None

    def _pool() -> list[dict]:
        nonlocal page_pool
        if page_pool is None:
            page_pool = rd_page_pool(rd_sources)
        return page_pool

    if config is None:
        for req in pending:
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NOT_CHECKED,
                detail="ключ ИИ не задан; совпадение обозначения не подтверждает требование; "
                       "смысловая сверка текста и просмотр листов не выполнялись",
            ))
        result.not_run.append("смысловая сверка текста РД и просмотр листов — нет ключа ИИ")
        return _finish(result)

    # --- Ступень 2: смысловая сверка текста РД моделью ---
    verdicts: dict[int, dict] = {}
    if llm_verify is None:
        # Г.107 — ключ есть, а смысловая сверка не подключена: шаг просто не
        # выполнялся. Без этой строки прогон выглядел бы так, будто модель
        # текст РД прочитала и ничего не подтвердила (Г.10/Г.77).
        result.not_run.append(
            "смысловая сверка текста РД — шаг не подключён в этом прогоне")
    if llm_verify is not None:
        _report("смысловая сверка текста РД моделью")
        try:
            for v in llm_verify(pending, rd_text_facts, config):
                idx = v.get("requirement_id")
                if type(idx) is int and 1 <= idx <= len(pending):
                    verdicts[idx] = v
                else:
                    # Старые адаптеры без id допустимы только при однозначном соответствии.
                    matches = [i for i, r in enumerate(pending, 1)
                               if r.sentence == v.get("sentence")
                               and ("page" not in v or r.page == v["page"])]
                    if len(matches) == 1:
                        verdicts[matches[0]] = v
        except Exception as exc:  # noqa: BLE001 — сбой не должен стать «в РД нет»
            for req in pending:
                result.items.append(ComplianceItem(
                    requirement=req, status=STATUS_NOT_CHECKED,
                    detail=f"смысловая сверка текста не выполнена: {type(exc).__name__}: {exc}",
                ))
            result.not_run.append(f"смысловая сверка текста РД: {type(exc).__name__}: {exc}")
            return _finish(result)

    still_pending: list[Requirement] = []
    text_notes: dict[int, str] = {}
    for idx, req in enumerate(pending, 1):
        v = verdicts.get(idx)
        evidence = validated_verdict_evidence(v, rd_text_facts) if v else []
        requires_attention = v and (
            v.get("failed_chunks")
            or (v.get("verdict") in ("absent", "conflicting") and evidence)
        )
        if requires_attention:
            citations = "; ".join(
                f"{e.get('document') or 'РД'}, стр.{e['page']} [F{e['fact_id']}]: «{e['quote']}»"
                for e in evidence
            )
            if v.get("verdict") == "conflicting":
                detail = f"текст РД содержит противоречивые сведения: {v.get('reason', '')}"
            else:
                detail = f"требуется ручная проверка по тексту РД: {v.get('reason', '')}"
            if citations:
                detail += f". {citations}"
            result.items.append(ComplianceItem(
                requirement=req, status=(STATUS_NOT_CHECKED
                    if v.get("failed_chunks") and not v.get("chunks_checked")
                    else STATUS_NEEDS_CHECK),
                detail=detail, evidence=citations,
            ))
            if v.get("failed_chunks"):
                result.not_run.append(
                    f"смысловая сверка текста РД: ошибок {v['failed_chunks']}; "
                    "часть корпуса не проверена")
            continue
        if v and v.get("verdict") == "confirmed" and evidence:
            citations = "; ".join(
                f"{e.get('document') or 'РД'}, стр.{e['page']} [F{e['fact_id']}]: «{e['quote']}»"
                for e in evidence
            )
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_CONFIRMED,
                detail=f"подтверждено по тексту РД: {v.get('reason', '')}. {citations}",
                evidence=citations,
            ))
        else:
            if v and v.get("reason"):
                text_notes[id(req)] = str(v["reason"])
            still_pending.append(req)

    _report("смысловая сверка текста РД")

    # --- Ступень 3: зрение по листам РД ---
    for req in still_pending:
        _report("просмотр листов РД")
        pages: list[tuple[str, int]] = []
        # Г.106 — якорем может быть и подсказка по названию помещения, если
        # номер в тексте требования не назван. Она честно помечается: номер в
        # скобках это факт документа, совпадение по названию — догадка, и
        # инспектор должен видеть, по чему ему подобрали лист.
        anchor_rooms = req.rooms or req.rooms_by_name
        by_name = not req.rooms and bool(req.rooms_by_name)
        anchor_note = (" (помещения подсказаны по названию, номер в требовании не назван)"
                       if by_name else "")
        if anchor_rooms and room_index is not None:
            available = {(e["path"], e["page"]) for room in anchor_rooms
                         for e in room_index.get(str(room), [])}
            result.diagnostics["candidates_available"] += len(available)
            pages = [(e["path"], e["page"]) for e in select_candidate_pages(
                anchor_rooms, room_index, max_visual_pages, req.sentence)]
        elif anchor_rooms and candidate_pages is not None:
            available = list(dict.fromkeys(candidate_pages(anchor_rooms, rd_sources)))
            result.diagnostics["candidates_available"] += len(available)
            pages = available[:max(0, max_visual_pages)]
        # Требование к объекту или системе целиком: реестр помещений лист
        # подобрать не может, но «не за что зацепиться реестром» — это не
        # «смотреть нечего». Листы берутся по близости текста листа к тексту
        # требования, а лист без текстового слоя остаётся кандидатом с
        # нулевым весом: иначе именно графика, ради которой проверка и
        # нужна, выпадала бы первой (Г.8/Г.10).
        by_requirement_text = False
        if not pages:
            fallback = rank_pool_for_requirement(_pool(), req.sentence, max_visual_pages)
            if fallback:
                by_requirement_text = True
                result.diagnostics["candidates_available"] += len(_pool())
                pages = [(entry["path"], entry["page"]) for entry in fallback]
        result.diagnostics["candidates_selected"] += len(pages)
        candidate_keys.update(pages)
        result.diagnostics["unique_candidate_pages"] = len(candidate_keys)

        if pages and vision_check is None:
            # Г.106 — листы подобраны, но смотреть их нечем. Раньше этого
            # состояния не существовало: подбор был завязан на наличие
            # зрения, и прогон без него докладывал «подходящих листов РД не
            # найдено» — то есть выдавал НЕВЫПОЛНЕННЫЙ шаг за отрицательный
            # результат поиска. Ровно та подмена, которую запрещает Г.10, и
            # заодно потеря главного, что нужно инспектору: куда смотреть.
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NEEDS_CHECK,
                detail="в тексте РД не подтверждено; листы для просмотра подобраны, "
                       "но просмотр изображения не выполнялся"
                       + (_TEXT_PICK_NOTE if by_requirement_text else anchor_note),
                pages_to_check=pages))
            continue

        if not pages:
            # Требование к объекту целиком: лист выбирать не по чему. Гонять
            # зрение вслепую по всему тому дороже и бесполезнее, чем сказать
            # инспектору прямо, что здесь нужен его глаз.
            reason = ("в тексте РД не подтверждено; в рабочей документации нет ни "
                      "одного листа, который можно было бы показать"
                      if not anchor_rooms else
                      "в тексте РД не подтверждено; подходящих листов РД не найдено"
                      + anchor_note)
            result.items.append(ComplianceItem(
                requirement=req, status=STATUS_NEEDS_CHECK, detail=reason))
            continue

        seen: dict | None = None
        errors = 0
        zooms_used = 0
        for pdf_path, page_no in pages:
            _report("просмотр листов РД")
            result.diagnostics["vision_calls"] += 1
            vision_keys.add((pdf_path, page_no))
            result.diagnostics["unique_vision_pages"] = len(vision_keys)
            pair = (pdf_path, page_no, req.sentence, tuple(req.rooms))
            result.diagnostics["repeated_vision_pairs"] += int(pair in vision_pairs)
            vision_pairs.add(pair)
            started = perf_counter()
            try:
                # Г.106 — модели передаётся `req.rooms`, а НЕ `anchor_rooms`:
                # подсказка по названию выбирает, какой лист открыть, но сама
                # в вопрос к модели не входит. Иначе догадка «похоже, речь об
                # этих помещениях» пришла бы к модели утверждением, и её
                # ответ подтверждал бы не требование ПД, а нашу же гипотезу.
                # Тест: test_vision_is_not_told_the_guessed_rooms.
                seen = vision_check(pdf_path, page_no, req.sentence, req.rooms, config)
            except Exception as exc:  # noqa: BLE001 — один лист не роняет прогон
                seen = {"verdict": "unclear", "reason": f"{type(exc).__name__}: {exc}",
                        "error": True}
            finally:
                result.diagnostics["vision_seconds"] += perf_counter() - started
            if not isinstance(seen, dict) or seen.get("verdict") not in {
                "confirmed", "absent", "unclear"
            }:
                seen = {"verdict": "unclear", "error": True}
            if seen.get("error"):
                errors += 1
                result.diagnostics["vision_errors"] += 1
                continue
            # Модель сама называет, что стоит рассмотреть ближе. Увеличение
            # идёт по ЕЁ зонам, а не по заранее вычисленной геометрии: на
            # плотном листе решение тонет целиком, и какая именно часть
            # нужна, видно только тому, кто лист уже посмотрел (раздел 6
            # задания). Подтверждённое не переспрашиваем — увеличение здесь
            # механизм полноты, а не сомнения в утвердительном ответе.
            if (seen.get("verdict") != "confirmed" and seen.get("candidate_regions")
                    and zooms_used < MAX_REQUIREMENT_ZOOMS):
                for region in seen["candidate_regions"][:MAX_REQUIREMENT_ZOOMS - zooms_used]:
                    zooms_used += 1
                    result.diagnostics["vision_calls"] += 1
                    result.diagnostics["zoom_calls"] += 1
                    started = perf_counter()
                    try:
                        closer = vision_check(pdf_path, page_no, req.sentence,
                                              req.rooms, config,
                                              clip_frac=region["rd_bbox_norm"])
                    except Exception as exc:  # noqa: BLE001 — кроп не роняет прогон
                        closer = {"verdict": "unclear", "error": True,
                                  "reason": f"{type(exc).__name__}: {exc}"}
                    finally:
                        result.diagnostics["vision_seconds"] += perf_counter() - started
                    if not isinstance(closer, dict) or closer.get("error"):
                        # Сорванный кроп не меняет вердикта целого листа, но и
                        # исчезнуть не может: неудавшийся вызов обязан быть
                        # видимым состоянием, а не молчаливым «как было» (Г.10).
                        result.diagnostics["vision_errors"] += 1
                        continue
                    if closer.get("verdict") in {"confirmed", "absent"}:
                        seen = closer
                        break
            if seen.get("verdict") == "unclear":
                result.diagnostics["vision_unclear"] += 1
            if seen.get("verdict") == "confirmed":
                result.items.append(ComplianceItem(
                    requirement=req, status=STATUS_CONFIRMED,
                    detail=f"подтверждено на листе: {seen.get('reason', '')}".strip(),
                    evidence=str(seen.get("where", "")),
                    pages_to_check=pages,
                ))
                break
        else:
            if errors == len(pages):
                detail = "просмотр листов не выполнялся успешно — ошибка зрения; проверить вручную"
                status = STATUS_NOT_CHECKED
            elif errors:
                detail = "листы просмотрены частично: часть вызовов завершилась ошибкой; проверить вручную"
                status = STATUS_NEEDS_CHECK
            else:
                detail = ("на просмотренных листах подтверждения нет — посмотреть глазами"
                          + (_TEXT_PICK_NOTE if by_requirement_text else ""))
                status = STATUS_NEEDS_CHECK
            if errors:
                result.not_run.append(f"просмотр листов: ошибок {errors} из {len(pages)}")
            result.items.append(ComplianceItem(
                requirement=req, status=status, detail=detail, pages_to_check=pages,
            ))
    for item in result.items:
        if id(item.requirement) in text_notes:
            item.detail += "; результат текстовой проверки: " + text_notes[id(item.requirement)]
    return _finish(result)


def _finish(result: ComplianceResult) -> ComplianceResult:
    result.counts = dict(Counter(i.status for i in result.items))
    return result


def render_compliance_report(result: ComplianceResult) -> str:
    """Отчёт для инспектора — в том же виде, что сводка разбора (Г.117).

    Обязан начинаться с оговорки о статусе выводов: это гипотезы для
    проверки на объекте, а не заключение о нарушении (Б.6). Без неё
    «требует проверки» на 60 позициях читается как 60 нарушений, чего в
    данных нет и быть не может — в РД мало текста по природе.

    Порядок разделов — по тому, что инспектору делать. Сначала то, где
    названы листы: их можно открыть и посмотреть. Затем то, где смотреть
    не по чему, — это вопрос к полноте данных, а не к объекту. Внутри —
    группировка по разделу и тому, как в сводке разбора: сплошной список
    на комплекте из нескольких томов нечитаем.
    """
    lines = [
        "=== Соответствие рабочей документации требованиям проектной ===",
        "Это НЕ ЯВЛЯЕТСЯ ЗАКЛЮЧЕНИЕМ о нарушениях. «Требует проверки» означает,",
        "что подтверждения не нашлось в доступных данных: в рабочей документации",
        "связного текста мало по природе (подписи чертежей переведены в кривые),",
        "и решение принимает инспектор, посмотрев названные листы.",
        "",
    ]
    for status in (STATUS_CONFIRMED, STATUS_NEEDS_CHECK, STATUS_NOT_CHECKED):
        lines.append(f"{status}: {result.counts.get(status, 0)}")
    if result.not_run:
        lines.append("")
        lines.append("Не выполнялось в этом прогоне:")
        lines.extend(f"  - {x}" for x in result.not_run)

    with_pages = [i for i in result.items
                  if i.status == STATUS_NEEDS_CHECK and i.pages_to_check]
    without_pages = [i for i in result.items
                     if i.status == STATUS_NEEDS_CHECK and not i.pages_to_check]
    for title, group in (
        (f"смотреть листы ({len(with_pages)})", with_pages),
        (f"листы выбрать не по чему ({len(without_pages)})", without_pages),
        (f"{STATUS_NOT_CHECKED} ({len([i for i in result.items if i.status == STATUS_NOT_CHECKED])})",
         [i for i in result.items if i.status == STATUS_NOT_CHECKED]),
        (f"{STATUS_CONFIRMED} ({len([i for i in result.items if i.status == STATUS_CONFIRMED])})",
         [i for i in result.items if i.status == STATUS_CONFIRMED]),
    ):
        if not group:
            continue
        lines.append("")
        lines.append(f"--- {title} ---")
        lines.extend(_render_group(group, result.display_names))
    return "\n".join(lines)


def _render_group(items: list[ComplianceItem], names: dict[str, str]) -> list[str]:
    """Группировка по разделу и тому — как в сводке разбора."""
    by_section: dict[tuple[str, str], list[ComplianceItem]] = {}
    for item in items:
        key = (item.requirement.section or "", item.requirement.document or "")
        by_section.setdefault(key, []).append(item)

    out: list[str] = []
    for (section, document), group in sorted(by_section.items()):
        head = f"[{section}]" if section else "[раздел не определён]"
        out.append(f"  {head} {document or 'файл не указан'} — {len(group)}")
        for item in sorted(group, key=lambda i: i.requirement.page):
            req = item.requirement
            out.append(f"    стр.{req.page}: {req.summary or req.sentence}")
            out.append(f"        {item.detail}")
            if item.pages_to_check:
                where = ", ".join(
                    f"{names.get(path, Path(path).name)}, лист {page}"
                    for path, page in item.pages_to_check)
                out.append(f"        смотреть: {where}")
    return out
