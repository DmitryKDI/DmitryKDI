"""Stateful GigaChat investigator for PD -> RD/ID comparison.

Instead of hundreds of isolated micro-prompts, one investigation keeps a
textual conversation history. Python exposes deterministic tools (page open,
text search, zoom). GigaChat decides what to inspect next. Candidate findings
are always re-checked by an independent verifier before becoming findings.

The runtime is blind-safe: benchmark ground truth is never accepted as input,
and learned lessons are disabled automatically when NADZOR_BLIND_BENCHMARK=1.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .conversation_llm import append_turn, call_conversation_json
from .inspector_memory import blind_mode, lessons_prompt
from .llm import LlmConfig, call_llm_json
from .matching import DocumentInput
from .requirement_registry import Requirement
from .semantic_contract import (
    NOT_OBSERVED_ON_THIS_EVIDENCE,
    OBSERVED_CONTRADICTION,
    WRONG_OR_INSUFFICIENT_SCOPE,
    normalize_bbox,
    normalize_difference_kind,
)
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_TURNS = _int_env("NADZOR_INVESTIGATOR_MAX_TURNS", 24, 4, 40)
MAX_PAGES_PER_ACTION = _int_env("NADZOR_INVESTIGATOR_PAGES_PER_ACTION", 4, 1, 6)
MAX_REGIONS_PER_ACTION = _int_env("NADZOR_INVESTIGATOR_REGIONS_PER_ACTION", 3, 1, 4)
MAX_VERIFY_REFS_PER_SIDE = _int_env("NADZOR_INVESTIGATOR_VERIFY_REFS", 2, 1, 3)
PAGE_HINT_CHARS = _int_env("NADZOR_INVESTIGATOR_PAGE_HINT_CHARS", 420, 120, 1200)
PAGE_TEXT_CHARS = _int_env("NADZOR_INVESTIGATOR_PAGE_TEXT_CHARS", 7000, 1000, 14000)


INVESTIGATOR_SYSTEM = f"""Ты — один ведущий AI-инспектор строительной документации.
Твоя задача — самостоятельно исследовать ПД и РД/ИД и найти инженерно
значимые изменения. У тебя есть память всего текущего расследования и набор
инструментов. Не пытайся решить всё одним ответом: выбирай следующий лучший
шаг, изучай evidence, помни уже увиденное и только потом предлагай finding.

{UNTRUSTED_INPUT_RULE}

ГЛАВНЫЕ ПРАВИЛА:
- Python routing/search — только навигация, не доказательство.
- «Не вижу на этом листе» != «этого нет в РД».
- Схема и план могут описывать одно решение разными способами.
- Если requirement относится к нескольким помещениям/зонам, нельзя считать
  его выполненным после проверки только одной из них.
- Не завершай расследование, пока остаются очевидные непросмотренные
  high-value evidence: требования ПД, релевантные листы, легенды, узлы,
  спецификации, соседние листы или спорные зоны.
- Не экономь ходы ради краткости. Цена пропуска сейчас выше цены дополнительной
  проверки.
- Любой proposed finding потом будет независимо проверен вторым вызовом модели.

В КАЖДОМ ответе выбери РОВНО ОДНО action и верни только JSON.

1) Найти страницы по смыслу/тегам:
{{"action":"search","query":"...","reason":"..."}}

2) Открыть до {MAX_PAGES_PER_ACTION} страниц целиком:
{{"action":"inspect_pages","refs":["PD0:P1","RD0:P3"],"reason":"..."}}

3) Увеличить до {MAX_REGIONS_PER_ACTION} зон:
{{"action":"zoom","regions":[
  {{"ref":"RD0:P3","bbox":[0.1,0.2,0.4,0.5],"reason":"..."}}
],"reason":"..."}}

4) Зафиксировать КАНДИДАТА расхождения и продолжить:
{{"action":"propose_finding","finding":{{
  "title":"...",
  "difference":"...",
  "difference_kind":"presence_absence|configuration|connection|parameter|topology|quantity|location|coverage|other",
  "pd_refs":["PD0:P1"],
  "rd_refs":["RD0:P3"],
  "pd_observation":"что наблюдается в ПД",
  "rd_observation":"что наблюдается в РД",
  "pd_bbox":[0.1,0.2,0.4,0.5],
  "rd_bbox":[0.2,0.2,0.5,0.5],
  "requirement_ids":["R1"],
  "why_it_matters":"..."
}}}}

5) Завершить, только если действительно исчерпаны разумные проверки:
{{"action":"finish","summary":"...","requirements_checked":["R1"],"unresolved":[]}}

Не выдавай сложный evidence-contract на каждом ходу. Твоя роль здесь —
ИССЛЕДОВАТЕЛЬ: решать, куда смотреть дальше, помнить контекст и собирать
кандидатов. Доказательность проверит отдельный verifier.
"""


VERIFY_SYSTEM = f"""Ты — независимый verifier кандидата расхождения ПД↔РД/ИД.
Ты НЕ продолжаешь расследование и не доверяешь выводу investigator на слово.
Смотри на приложенные evidence и реши, есть ли прямое наблюдаемое
противоречие.

{UNTRUSTED_INPUT_RULE}

Правила:
- Нужны конкретное наблюдение ПД + конкретное наблюдение РД + прямое отличие.
- NOT_OBSERVED не повышай до ABSENCE.
- Для presence_absence confirmed допустим только если показанный scope
  действительно обязан и достаточно полно отображать проверяемый объект;
  иначе needs_more.
- Для требования на несколько помещений scope считается полным только если
  evidence покрывает все относящиеся к finding помещения/зоны либо finding
  явно ограничен конкретной проверенной частью требования.
- Разный жанр листа сам по себе не finding.
- Если изображения/подписи недостаточно читаемы — needs_more.

Ответ только JSON:
{{
  "verdict":"confirmed|needs_more|rejected",
  "state":"OBSERVED_CONTRADICTION|NOT_OBSERVED_ON_THIS_EVIDENCE|WRONG_OR_INSUFFICIENT_SCOPE",
  "pd_observation":"...",
  "rd_observation":"...",
  "difference":"...",
  "difference_kind":"presence_absence|configuration|connection|parameter|topology|quantity|location|coverage|other",
  "scope_sufficient":false,
  "absence_scope_complete":false,
  "reason":"..."
}}
"""


@dataclass
class InvestigatorResult:
    findings: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def _page_text(document: DocumentInput, page: int) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in document.text_facts
        if int(item.get("page") or 0) == page
    ).strip()


def _rooms(document: DocumentInput, page: int) -> list[str]:
    out = []
    for item in document.room_facts:
        if int(item.get("page") or 0) != page:
            continue
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        label = key if not name else f"{key} {name}".strip()
        if label and label not in out:
            out.append(label)
    return out[:20]


def _equipment(document: DocumentInput, page: int) -> list[str]:
    out = []
    for item in document.equipment_facts:
        if int(item.get("page") or 0) != page:
            continue
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        label = key if not name else f"{key} {name}".strip()
        if label and label not in out:
            out.append(label)
    return out[:20]


def build_page_catalog(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
) -> list[dict]:
    """Build a compact non-gating map of every available page."""
    rows: list[dict] = []
    for side, docs in (("PD", before_docs), ("RD", after_docs)):
        for doc_index, document in enumerate(docs):
            for page in range(1, int(document.pages) + 1):
                text = _page_text(document, page)
                rows.append(
                    {
                        "ref": f"{side}{doc_index}:P{page}",
                        "side": side,
                        "document_index": doc_index,
                        "page": page,
                        "document": document.name,
                        "page_kind": document.page_kinds.get(page, "unknown"),
                        "discipline": document.discipline_code or "",
                        "rooms": _rooms(document, page),
                        "equipment": _equipment(document, page),
                        "text_hint": " ".join(text.split())[:PAGE_HINT_CHARS],
                    }
                )
    return rows


def _ref_map(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for side, docs, paths in (
        ("PD", before_docs, before_paths),
        ("RD", after_docs, after_paths),
    ):
        for doc_index, document in enumerate(docs):
            if doc_index >= len(paths):
                continue
            for page in range(1, int(document.pages) + 1):
                out[f"{side}{doc_index}:P{page}"] = {
                    "side": side,
                    "document": document,
                    "document_index": doc_index,
                    "path": str(paths[doc_index]),
                    "page": page,
                }
    return out


_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё._/-]{2,}")


def _search(catalog: list[dict], query: str, limit: int = 10) -> list[dict]:
    terms = {token.casefold() for token in _TOKEN_RE.findall(query)}
    if not terms:
        return catalog[:limit]
    scored = []
    for row in catalog:
        haystack = " ".join(
            [
                str(row.get("document") or ""),
                str(row.get("page_kind") or ""),
                str(row.get("discipline") or ""),
                " ".join(row.get("rooms") or []),
                " ".join(row.get("equipment") or []),
                str(row.get("text_hint") or ""),
            ]
        ).casefold()
        score = sum(2 if term in haystack else 0 for term in terms)
        score += sum(1 for term in terms if any(part.startswith(term) for part in haystack.split()))
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], item[1]["ref"]))
    return [row for _, row in scored[:limit]]


def _requirements_payload(requirements: Sequence[Requirement]) -> list[dict]:
    out = []
    for index, req in enumerate(requirements, 1):
        out.append(
            {
                "id": f"R{index}",
                "document": req.document,
                "page": req.page,
                "rooms": list(req.rooms or req.rooms_by_name or []),
                "summary": req.summary or req.sentence,
                "source_text": (req.sentence or req.summary or "")[:900],
            }
        )
    return out


def _normalize_refs(value: object, ref_map: dict[str, dict], limit: int) -> list[str]:
    out = []
    for ref in value if isinstance(value, list) else []:
        key = str(ref or "").strip().upper()
        if key in ref_map and key not in out:
            out.append(key)
        if len(out) >= limit:
            break
    return out


def _normalize_candidate(value: object, ref_map: dict[str, dict]) -> dict | None:
    if not isinstance(value, dict):
        return None
    pd_refs = [ref for ref in _normalize_refs(value.get("pd_refs"), ref_map, 6) if ref.startswith("PD")]
    rd_refs = [ref for ref in _normalize_refs(value.get("rd_refs"), ref_map, 6) if ref.startswith("RD")]
    difference = " ".join(str(value.get("difference") or "").split()).strip()
    pd_observation = " ".join(str(value.get("pd_observation") or "").split()).strip()
    rd_observation = " ".join(str(value.get("rd_observation") or "").split()).strip()
    if not pd_refs or not rd_refs or not difference or not pd_observation or not rd_observation:
        return None

    pd_bbox = normalize_bbox(value.get("pd_bbox"))
    rd_bbox = normalize_bbox(value.get("rd_bbox"))
    req_ids = []
    for raw in value.get("requirement_ids") or []:
        text = str(raw or "").strip().upper()
        if re.fullmatch(r"R\d+", text) and text not in req_ids:
            req_ids.append(text)

    return {
        "title": " ".join(str(value.get("title") or "").split()).strip()[:240],
        "difference": difference[:1600],
        "difference_kind": normalize_difference_kind(value.get("difference_kind")),
        "pd_refs": pd_refs,
        "rd_refs": rd_refs,
        "pd_observation": pd_observation[:1600],
        "rd_observation": rd_observation[:1600],
        "pd_bbox": pd_bbox,
        "rd_bbox": rd_bbox,
        "requirement_ids": req_ids,
        "why_it_matters": " ".join(str(value.get("why_it_matters") or "").split()).strip()[:1200],
    }


def _compact_catalog(catalog: list[dict]) -> str:
    rows = []
    for row in catalog:
        rows.append(
            {
                "ref": row["ref"],
                "kind": row["page_kind"],
                "discipline": row["discipline"],
                "rooms": row["rooms"][:12],
                "equipment": row["equipment"][:12],
                "text_hint": row["text_hint"],
            }
        )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _tool_pages(refs: list[str], ref_map: dict[str, dict]) -> tuple[str, list[str]]:
    lines = ["TOOL RESULT: opened pages"]
    images: list[str] = []
    for ref in refs:
        item = ref_map[ref]
        document: DocumentInput = item["document"]
        page = int(item["page"])
        text = _page_text(document, page)[:PAGE_TEXT_CHARS]
        lines.append(
            f"\n[{ref}] kind={document.page_kinds.get(page,'unknown')} "
            f"discipline={document.discipline_code or ''}\n"
            f"TEXT LAYER:\n<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"
        )
        images.append(render_page_to_data_url(item["path"], page))
    return "\n".join(lines), images


def _tool_zoom(regions: list[dict], ref_map: dict[str, dict]) -> tuple[str, list[str], list[dict]]:
    lines = ["TOOL RESULT: zoomed regions"]
    images: list[str] = []
    accepted: list[dict] = []
    for raw in regions[:MAX_REGIONS_PER_ACTION]:
        if not isinstance(raw, dict):
            continue
        ref = str(raw.get("ref") or "").strip().upper()
        if ref not in ref_map:
            continue
        box = normalize_bbox(raw.get("bbox"))
        if box is None:
            continue
        area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
        if area < 0.005 or area > 0.65:
            continue
        item = ref_map[ref]
        page = int(item["page"])
        reason = " ".join(str(raw.get("reason") or "").split()).strip()
        images.append(render_page_to_data_url(item["path"], page))
        images.append(render_page_to_data_url(item["path"], page, clip_frac=box))
        accepted.append({"ref": ref, "bbox": box, "reason": reason})
        lines.append(f"{ref} bbox={box} reason={reason}")
    return "\n".join(lines), images, accepted


def _history_user_text(text: str, *, first_turn: bool) -> str:
    """Keep the session memory useful without replaying every full page forever."""
    if first_turn or len(text) <= 9000:
        return text
    return text[:8500] + "\n[tool payload compacted in conversation history]"


def _state_note(inspected_refs: set[str], candidates: list[dict], zoomed: list[dict]) -> str:
    refs = ",".join(sorted(inspected_refs))
    return (
        "\nSESSION NOTE: inspected_refs=["
        + refs
        + f"]; candidates_saved={len(candidates)}; zooms_done={len(zoomed)}."
    )


def _candidate_key(candidate: dict) -> tuple:
    return (
        tuple(candidate.get("pd_refs") or []),
        tuple(candidate.get("rd_refs") or []),
        str(candidate.get("difference") or "").casefold(),
    )


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for candidate in candidates:
        key = _candidate_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _verification_images(candidate: dict, ref_map: dict[str, dict]) -> tuple[list[str], list[str]]:
    images: list[str] = []
    evidence_refs: list[str] = []
    for side_key, bbox_key in (("pd_refs", "pd_bbox"), ("rd_refs", "rd_bbox")):
        refs = candidate.get(side_key) or []
        bbox = candidate.get(bbox_key)
        for ref in refs[:MAX_VERIFY_REFS_PER_SIDE]:
            item = ref_map.get(ref)
            if not item:
                continue
            page = int(item["page"])
            images.append(render_page_to_data_url(item["path"], page))
            evidence_refs.append(ref)
            if bbox is not None and len(refs) == 1:
                images.append(render_page_to_data_url(item["path"], page, clip_frac=bbox))
    return images[:10], evidence_refs


def _verify_candidate(
    config: LlmConfig,
    candidate: dict,
    ref_map: dict[str, dict],
    requirements_by_id: dict[str, dict],
) -> dict:
    images, evidence_refs = _verification_images(candidate, ref_map)
    related = [
        requirements_by_id[req_id]
        for req_id in candidate.get("requirement_ids") or []
        if req_id in requirements_by_id
    ]
    prompt = (
        "CANDIDATE FROM INVESTIGATOR:\n"
        + json.dumps(candidate, ensure_ascii=False, default=list)
        + "\nRELATED PD REQUIREMENTS:\n"
        + json.dumps(related, ensure_ascii=False)
        + "\nEVIDENCE REFS ATTACHED: "
        + ", ".join(evidence_refs)
    )
    try:
        result = call_llm_json(
            config,
            VERIFY_SYSTEM,
            prompt,
            images=images,
            operation="vision",
            source_digest="stateful-verifier:" + "|".join(evidence_refs),
            prompt_version="stateful-investigator-verifier-v1",
            use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "verdict": "needs_more",
            "state": WRONG_OR_INSUFFICIENT_SCOPE,
            "reason": f"verifier technical failure: {type(exc).__name__}: {exc}",
            "error": True,
        }
    return result if isinstance(result, dict) else {}


def _confirmed(candidate: dict, verification: dict) -> bool:
    if str(verification.get("verdict") or "").casefold() != "confirmed":
        return False
    if str(verification.get("state") or "").upper() != OBSERVED_CONTRADICTION:
        return False
    if not bool(verification.get("scope_sufficient")):
        return False
    if not str(verification.get("pd_observation") or "").strip():
        return False
    if not str(verification.get("rd_observation") or "").strip():
        return False
    if not str(verification.get("difference") or "").strip():
        return False
    kind = normalize_difference_kind(
        verification.get("difference_kind") or candidate.get("difference_kind")
    )
    if kind == "presence_absence" and not bool(verification.get("absence_scope_complete")):
        return False
    return True


def run_stateful_investigator(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    requirements: Sequence[Requirement],
    config: LlmConfig,
) -> InvestigatorResult:
    catalog = build_page_catalog(before_docs, after_docs)
    refs = _ref_map(before_docs, after_docs, before_paths, after_paths)
    req_payload = _requirements_payload(requirements)
    req_by_id = {row["id"]: row for row in req_payload}
    memory = lessons_prompt()

    initial = (
        "DOCUMENT MAP (navigation only, every page remains available):\n"
        + _compact_catalog(catalog)
        + "\n\nPD REQUIREMENTS EXTRACTED FROM TEXT:\n"
        + json.dumps(req_payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
        + memory
        + "\nНачни расследование. Выбери первый лучший action."
    )

    history: list[dict] = []
    candidates: list[dict] = []
    action_counts: Counter[str] = Counter()
    inspected_refs: set[str] = set()
    zoomed: list[dict] = []
    errors: list[str] = []
    transcript: list[dict] = []
    user_text = initial
    current_images: list[str] = []
    finish_requested = False
    self_reviewed = False
    finished = False

    for turn in range(1, MAX_TURNS + 1):
        try:
            effective_user_text = user_text + _state_note(inspected_refs, candidates, zoomed)
            response = call_conversation_json(
                config,
                INVESTIGATOR_SYSTEM,
                history,
                effective_user_text,
                images=current_images,
                timeout=240.0,
                operation="vision" if current_images else "text_verify",
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"turn {turn}: {type(exc).__name__}: {exc}")
            break

        append_turn(history, _history_user_text(effective_user_text, first_turn=(turn == 1)), response)
        action = str(response.get("action") or "").strip().casefold()
        action_counts[action or "invalid"] += 1
        transcript.append(
            {
                "turn": turn,
                "action": action,
                "reason": str(response.get("reason") or "")[:500],
                "summary": str(response.get("summary") or "")[:500],
            }
        )
        current_images = []

        if action == "search":
            query = str(response.get("query") or "").strip()
            matches = _search(catalog, query, limit=12)
            user_text = (
                "TOOL RESULT: semantic/lexical search. This only ranks navigation; "
                "it proves nothing.\nQUERY="
                + query
                + "\n"
                + json.dumps(matches, ensure_ascii=False, separators=(",", ":"))
                + "\nChoose next action."
            )
            continue

        if action == "inspect_pages":
            requested = _normalize_refs(response.get("refs"), refs, MAX_PAGES_PER_ACTION)
            if not requested:
                user_text = "TOOL ERROR: no valid refs. Use refs exactly as in DOCUMENT MAP."
                continue
            inspected_refs.update(requested)
            user_text, current_images = _tool_pages(requested, refs)
            user_text += "\nUse what you actually observe; choose next action."
            continue

        if action == "zoom":
            regions = response.get("regions") if isinstance(response.get("regions"), list) else []
            user_text, current_images, accepted = _tool_zoom(regions, refs)
            if not accepted:
                user_text = (
                    "TOOL ERROR: no valid local regions. bbox must be normalized and "
                    "must not be nearly whole-page. Choose a real local region or another page."
                )
                continue
            zoomed.extend(accepted)
            inspected_refs.update(row["ref"] for row in accepted)
            user_text += "\nGeneral + local images are attached in order. Choose next action."
            continue

        if action == "propose_finding":
            candidate = _normalize_candidate(response.get("finding"), refs)
            if candidate is None:
                user_text = (
                    "NOTEBOOK REJECTED CANDIDATE: missing direct PD/RD observations, "
                    "difference, or valid PD/RD refs. Inspect evidence and propose again."
                )
                continue
            candidates.append(candidate)
            candidates = _dedupe_candidates(candidates)
            user_text = (
                "NOTEBOOK: candidate saved for later independent verification. "
                "Do NOT stop just because one candidate exists. Continue searching for "
                "other changes and uncovered requirements."
            )
            continue

        if action == "finish":
            if not self_reviewed:
                finish_requested = True
                self_reviewed = True
                user_text = (
                    "SELF-REVIEW / RED TEAM BEFORE FINISHING:\n"
                    "You tried to finish. Audit your own search for false negatives. "
                    "Check especially: multi-room requirements where only one room was seen; "
                    "schematic-vs-plan scope; requirements with no inspected RD evidence; "
                    "legends/specifications/adjacent sheets not opened; unknown configuration "
                    "or topology changes; pages suggested by search but never inspected. "
                    "If any high-value uncertainty remains, use search/inspect_pages/zoom. "
                    "Only return finish again if no reasonable additional evidence should be inspected."
                )
                continue
            finished = True
            break

        user_text = (
            "INVALID ACTION. Return exactly one of search, inspect_pages, zoom, "
            "propose_finding, finish using the documented JSON schema."
        )

    candidates = _dedupe_candidates(candidates)
    findings: list[dict] = []
    verification_rows: list[dict] = []
    unresolved: list[dict] = []

    for candidate in candidates:
        verification = _verify_candidate(config, candidate, refs, req_by_id)
        row = {"candidate": candidate, "verification": verification}
        verification_rows.append(row)
        if _confirmed(candidate, verification):
            finding = {
                "source": "stateful_investigator",
                "domain": "semantic",
                "key": "|".join((candidate.get("pd_refs") or []) + (candidate.get("rd_refs") or [])),
                "detail": str(verification.get("difference") or candidate["difference"]),
                "state": OBSERVED_CONTRADICTION,
                "difference_kind": normalize_difference_kind(
                    verification.get("difference_kind") or candidate.get("difference_kind")
                ),
                "pd_observation": str(verification.get("pd_observation") or candidate["pd_observation"]),
                "rd_observation": str(verification.get("rd_observation") or candidate["rd_observation"]),
                "pd_refs": list(candidate.get("pd_refs") or []),
                "rd_refs": list(candidate.get("rd_refs") or []),
                "pd_bbox": candidate.get("pd_bbox"),
                "rd_bbox": candidate.get("rd_bbox"),
                "requirement_ids": list(candidate.get("requirement_ids") or []),
                "verified": True,
                "verification_reason": str(verification.get("reason") or ""),
            }
            findings.append(finding)
        else:
            unresolved.append(row)

    diagnostics = {
        "architecture": "stateful_investigator->python_tools->self_review->independent_verifier",
        "model": config.resolved_model(),
        "blind_mode": blind_mode(),
        "learned_lessons_enabled": not blind_mode(),
        "max_turns": MAX_TURNS,
        "turns_used": len(transcript),
        "finished": finished,
        "finish_requested": finish_requested,
        "self_reviewed": self_reviewed,
        "turn_budget_exhausted": not finished and len(transcript) >= MAX_TURNS,
        "action_counts": dict(action_counts),
        "pages_total": len(catalog),
        "pages_inspected": len(inspected_refs),
        "inspected_refs": sorted(inspected_refs),
        "zoom_regions_total": len(zoomed),
        "zoom_regions": zoomed,
        "requirements_total": len(requirements),
        "candidates_total": len(candidates),
        "confirmed_total": len(findings),
        "unresolved_total": len(unresolved),
        "verifier_errors": sum(
            1 for row in verification_rows
            if isinstance(row.get("verification"), dict)
            and bool(row["verification"].get("error"))
        ),
        "errors": errors,
        "transcript": transcript,
        "verification": verification_rows,
    }
    return InvestigatorResult(findings=findings, candidates=unresolved, diagnostics=diagnostics)
