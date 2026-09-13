"""TEXT requirement PD -> RD/ID evidence using the universal semantic contract.

This replaces the active compliance ladder inside the lean runtime.  Room/text
matching only ranks candidate sheets.  The semantic decision is one contract:
whole-page scope -> local regions -> observed contradiction / compliant /
not-observed / insufficient scope.
"""
from __future__ import annotations

import hashlib
import os
from time import perf_counter

from .compliance import (
    ComplianceItem,
    ComplianceResult,
    STATUS_CONFIRMED,
    STATUS_NEEDS_CHECK,
    STATUS_NOT_CHECKED,
)
from .llm import LlmConfig, call_llm_json
from .requirement_registry import Requirement
from .semantic_contract import (
    APPEARS_COMPLIANT,
    NOT_OBSERVED_ON_THIS_EVIDENCE,
    OBSERVED_CONTRADICTION,
    WRONG_OR_INSUFFICIENT_SCOPE,
    finding_can_confirm,
    normalize_findings,
    normalize_regions,
    normalize_sheet_type,
    normalize_state,
)
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url
from .vision_page_compare import rank_pool_for_requirement, rd_page_pool, select_candidate_pages


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_REQUIREMENT_PAGES = _int_env("NADZOR_MAX_REQUIREMENT_PAGES", 3, 1, 6)
MAX_REQUIREMENT_ZOOMS = _int_env("NADZOR_MAX_REQUIREMENT_ZOOMS", 3, 1, 4)
MIN_REQUIREMENT_LOCAL_CHECKS = _int_env("NADZOR_MIN_REQUIREMENT_LOCAL_CHECKS", 1, 1, 3)

_SYSTEM = f"""Ты проверяешь конкретный engineering requirement из ПД по evidence РД/ИД.
Требование может быть текстом, а evidence — текстовым слоем и изображением
листа РД. Работай только по наблюдаемым данным.

{UNTRUSTED_INPUT_RULE}

Используй те же evidence_state, что и для drawing->drawing:
OBSERVED_CONTRADICTION, NOT_OBSERVED_ON_THIS_EVIDENCE,
WRONG_OR_INSUFFICIENT_SCOPE, APPEARS_COMPLIANT.

КРИТИЧЕСКОЕ ПРАВИЛО: «не вижу на этом листе» НЕ означает «отсутствует в РД».
Если sheet_type/масштаб/охват не обязаны показывать требуемый элемент, верни
WRONG_OR_INSUFFICIENT_SCOPE либо NOT_OBSERVED_ON_THIS_EVIDENCE и попроси
additional evidence. Presence/absence contradiction допустим только после
local zoom и достаточного scope; без independent corroboration он остаётся
кандидатом, а не подтверждённым отсутствием.

Сначала определи sheet_type: SCHEMATIC|PLAN|RCP|DETAIL|SCHEDULE|UNKNOWN,
readability и evidence_scope. Затем найди положительное evidence требования
или прямое противоречие. Отсутствие слова в text layer ничего не доказывает.

Если whole-page ответ не разрешает вопрос, предложи до 3 локальных зон.
Каждая зона 2-40% страницы, без whole-page bbox [0,0,1,1] и без сильного
перекрытия. В local режиме проверяй увеличенную область, general image только
контекст.

Finding contract тот же:
state=OBSERVED_CONTRADICTION, pd_claim, pd_evidence_ref, rd_claim,
rd_evidence_ref, difference, difference_kind, evidence_scope,
absence_verified, corroboration_refs, requires_additional_evidence.

Ответ только JSON:
{{
 "evidence_state":"OBSERVED_CONTRADICTION|NOT_OBSERVED_ON_THIS_EVIDENCE|WRONG_OR_INSUFFICIENT_SCOPE|APPEARS_COMPLIANT",
 "comparability":"high|medium|low",
 "rd_sheet":{{"sheet_type":"SCHEMATIC|PLAN|RCP|DETAIL|SCHEDULE|UNKNOWN","scale":"","level":"","orientation":""}},
 "requirement_evidence":{{"observed":false,"rd_evidence_ref":"","where":""}},
 "findings":[{{"state":"OBSERVED_CONTRADICTION","pd_claim":"...","pd_evidence_ref":"PD requirement text","rd_claim":"...","rd_evidence_ref":"...","difference":"...","difference_kind":"configuration","where":"...","evidence_scope":"complete|partial|unknown","absence_verified":false,"corroboration_refs":[],"requires_additional_evidence":false}}],
 "candidate_regions":[{{"reason":"...","rd_bbox_norm":[0.1,0.1,0.4,0.4],"priority":"high|medium|low"}}],
 "coverage_notes":["..."],"uncertainties":["..."],"additional_evidence_needed":["..."]
}}"""

_DISCOVERY = """
REGION DISCOVERY: whole-page не дал достаточного локального evidence. Назови
до 3 разных локальных зон 2-40% страницы. Whole-page bbox запрещён. Если
правильный жанр/вид отсутствует, верни WRONG_OR_INSUFFICIENT_SCOPE вместо
догадки.
"""

_LOCAL = """
LOCAL VERIFICATION: вывод делай по увеличенной зоне. NOT_OBSERVED не повышай
до ABSENCE. Если требование явно реализовано, верни APPEARS_COMPLIANT и точный
rd_evidence_ref. Если есть прямое противоречие — OBSERVED_CONTRADICTION.
"""


def _comparability(result: dict) -> str:
    value = str(result.get("comparability") or "").strip().casefold()
    return value if value in {"high", "medium", "low"} else "low"


def _sheet(value: object) -> dict:
    row = value if isinstance(value, dict) else {}
    return {
        "sheet_type": normalize_sheet_type(row.get("sheet_type")),
        "scale": str(row.get("scale") or "").strip(),
        "level": str(row.get("level") or "").strip(),
        "orientation": str(row.get("orientation") or "").strip(),
    }


def _page_candidates(
    req: Requirement,
    rd_sources: list[tuple[str, str]],
    room_index: dict[str, list[dict]] | None,
    page_pool: list[dict],
    max_pages: int,
) -> list[dict]:
    anchor_rooms = list(req.rooms or req.rooms_by_name or [])
    if anchor_rooms and room_index:
        selected = select_candidate_pages(anchor_rooms, room_index, max_pages, req.sentence)
        if selected:
            return selected
    return rank_pool_for_requirement(page_pool, req.sentence, max_pages)


def _call(
    config: LlmConfig,
    req: Requirement,
    entry: dict,
    *,
    region: dict | None = None,
    discover: bool = False,
) -> dict:
    path = str(entry["path"])
    page = int(entry["page"])
    general = render_page_to_data_url(path, page)
    images = [general]
    mode = ""
    if region is not None:
        local = render_page_to_data_url(path, page, clip_frac=region["rd_bbox_norm"])
        images = [general, local]
        mode = f"\n{_LOCAL}\nregion={region['rd_bbox_norm']}; reason={region.get('reason','')}"
    elif discover:
        mode = "\n" + _DISCOVERY

    requirement = req.summary or req.sentence
    page_text = str(entry.get("text") or "")[:5000]
    user_text = (
        "PD REQUIREMENT:\n"
        f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{requirement}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"PD source: document={req.document}; page={req.page}; discipline={req.section or ''}; "
        f"rooms explicitly stated={list(req.rooms or [])}.\n"
        "RD PAGE TEXT LAYER:\n"
        f"<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{page_text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"RD source: document={entry.get('name','')}; page={page}.{mode}"
    )
    digest = hashlib.sha256(
        f"{path}:{page}:{requirement}:{region}:{discover}:requirement-semantic-v1".encode()
    ).hexdigest()
    result = call_llm_json(
        config,
        _SYSTEM,
        user_text,
        images=images,
        operation="vision",
        source_digest=digest,
        prompt_version="requirement-semantic-v1-universal-contract",
    )
    return result if isinstance(result, dict) else {}


def _texts(value: object) -> list[str]:
    return [str(x).strip() for x in value if str(x).strip()] if isinstance(value, list) else []


def check_requirements_semantic(
    requirements: list[Requirement],
    rd_sources: list[tuple[str, str]],
    config: LlmConfig | None,
    *,
    room_index: dict[str, list[dict]] | None = None,
    max_pages: int = MAX_REQUIREMENT_PAGES,
) -> ComplianceResult:
    result = ComplianceResult()
    diagnostics: dict = {
        "architecture": "requirement->candidate_evidence->whole_page_scope->local_verification->universal_evidence_contract",
        "requirements_total": len(requirements),
        "candidates_available": 0,
        "candidates_selected": 0,
        "unique_candidate_pages": 0,
        "vision_calls": 0,
        "zoom_calls": 0,
        "region_discovery_calls": 0,
        "vision_errors": 0,
        "vision_unclear": 0,
        "vision_seconds": 0.0,
        "results": [],
    }
    result.diagnostics = diagnostics
    if not requirements:
        result.counts = {}
        return result
    if config is None or not config.api_key:
        result.not_run.append("semantic requirement verification — no AI key")
        for req in requirements:
            result.items.append(ComplianceItem(req, STATUS_NOT_CHECKED, "semantic verification did not run"))
        result.counts = {STATUS_NOT_CHECKED: len(result.items)}
        return result

    pool = rd_page_pool(rd_sources)
    unique_pages: set[tuple[str, int]] = set()

    for req_index, req in enumerate(requirements, 1):
        pages = _page_candidates(req, rd_sources, room_index, pool, max_pages)
        diagnostics["candidates_available"] += len(pool)
        diagnostics["candidates_selected"] += len(pages)
        unique_pages.update((str(x.get("path")), int(x.get("page") or 0)) for x in pages)
        diagnostics["unique_candidate_pages"] = len(unique_pages)
        req_record = {
            "requirement_index": req_index,
            "requirement": req.summary or req.sentence,
            "pd_document": req.document,
            "pd_page": req.page,
            "rooms": list(req.rooms or []),
            "pages": [],
            "final_state": WRONG_OR_INSUFFICIENT_SCOPE,
            "confirmed_evidence": None,
            "candidate_findings": [],
            "additional_evidence_needed": [],
        }
        if not pages:
            result.items.append(ComplianceItem(
                req, STATUS_NEEDS_CHECK,
                "не найдено подходящего evidence-листа РД; это не означает отсутствие решения",
            ))
            diagnostics["results"].append(req_record)
            continue

        compliant_evidence: dict | None = None
        candidate_findings: list[dict] = []
        had_success = False
        had_error = False
        best_state = WRONG_OR_INSUFFICIENT_SCOPE

        for entry in pages:
            page_record = {
                "document": entry.get("name"),
                "page": entry.get("page"),
                "whole_page": None,
                "candidate_regions": [],
                "checked_regions": [],
            }
            started = perf_counter()
            diagnostics["vision_calls"] += 1
            try:
                whole = _call(config, req, entry)
                had_success = True
            except Exception as exc:  # noqa: BLE001
                whole = {"evidence_state": WRONG_OR_INSUFFICIENT_SCOPE, "comparability": "low", "error": f"{type(exc).__name__}: {exc}"}
                diagnostics["vision_errors"] += 1
                had_error = True
            finally:
                diagnostics["vision_seconds"] += perf_counter() - started

            state = normalize_state(whole.get("evidence_state") or whole.get("state"))
            comp = _comparability(whole)
            findings = normalize_findings(whole.get("findings"))
            regions = normalize_regions(whole.get("candidate_regions"))
            evidence = whole.get("requirement_evidence") if isinstance(whole.get("requirement_evidence"), dict) else {}
            page_record["whole_page"] = {
                "evidence_state": state,
                "comparability": comp,
                "rd_sheet": _sheet(whole.get("rd_sheet")),
                "requirement_evidence": evidence,
                "findings": findings,
                "uncertainties": _texts(whole.get("uncertainties")),
                "additional_evidence_needed": _texts(whole.get("additional_evidence_needed")),
            }

            # Whole-page contradictions are candidates only.  Whole-page
            # compliance also needs a local evidence check before it can close
            # the requirement on a dense drawing.
            candidate_findings.extend(findings)
            if state == OBSERVED_CONTRADICTION:
                best_state = OBSERVED_CONTRADICTION
            elif state == NOT_OBSERVED_ON_THIS_EVIDENCE and best_state != OBSERVED_CONTRADICTION:
                best_state = NOT_OBSERVED_ON_THIS_EVIDENCE
            elif state == APPEARS_COMPLIANT and best_state not in {OBSERVED_CONTRADICTION, NOT_OBSERVED_ON_THIS_EVIDENCE}:
                best_state = APPEARS_COMPLIANT

            if len(regions) < MIN_REQUIREMENT_LOCAL_CHECKS:
                diagnostics["region_discovery_calls"] += 1
                started = perf_counter()
                try:
                    discovered = _call(config, req, entry, discover=True)
                    regions = normalize_regions(discovered.get("candidate_regions"))
                    req_record["additional_evidence_needed"].extend(_texts(discovered.get("additional_evidence_needed")))
                except Exception as exc:  # noqa: BLE001
                    diagnostics["vision_errors"] += 1
                    had_error = True
                    page_record.setdefault("errors", []).append(f"region discovery: {type(exc).__name__}: {exc}")
                finally:
                    diagnostics["vision_seconds"] += perf_counter() - started

            page_record["candidate_regions"] = regions
            for region in regions[:MAX_REQUIREMENT_ZOOMS]:
                diagnostics["zoom_calls"] += 1
                diagnostics["vision_calls"] += 1
                started = perf_counter()
                try:
                    local = _call(config, req, entry, region=region)
                    had_success = True
                except Exception as exc:  # noqa: BLE001
                    local = {"evidence_state": WRONG_OR_INSUFFICIENT_SCOPE, "comparability": "low", "error": f"{type(exc).__name__}: {exc}"}
                    diagnostics["vision_errors"] += 1
                    had_error = True
                finally:
                    diagnostics["vision_seconds"] += perf_counter() - started

                local_state = normalize_state(local.get("evidence_state") or local.get("state"))
                local_comp = _comparability(local)
                local_findings = normalize_findings(local.get("findings"))
                local_evidence = local.get("requirement_evidence") if isinstance(local.get("requirement_evidence"), dict) else {}
                page_record["checked_regions"].append({
                    "rd_bbox_norm": region.get("rd_bbox_norm"),
                    "reason": region.get("reason"),
                    "evidence_state": local_state,
                    "comparability": local_comp,
                    "findings": local_findings,
                    "requirement_evidence": local_evidence,
                    "uncertainties": _texts(local.get("uncertainties")),
                })

                if local_state == OBSERVED_CONTRADICTION:
                    best_state = OBSERVED_CONTRADICTION
                    for finding in local_findings:
                        if finding_can_confirm(
                            finding,
                            comparability=local_comp,
                            local_verified=True,
                            scope_state=local_state,
                        ):
                            # A fully confirmed non-absence contradiction is
                            # still shown to the inspector as a discrepancy,
                            # not as 'requirement confirmed'.
                            finding = {**finding, "local_verified": True, "confirmed": True}
                        candidate_findings.append(finding)
                elif (
                    local_state == APPEARS_COMPLIANT
                    and local_comp == "high"
                    and bool(local_evidence.get("observed"))
                    and str(local_evidence.get("rd_evidence_ref") or "").strip()
                ):
                    compliant_evidence = {
                        "document": entry.get("name"),
                        "page": entry.get("page"),
                        "bbox": region.get("rd_bbox_norm"),
                        "rd_evidence_ref": str(local_evidence.get("rd_evidence_ref")),
                        "where": str(local_evidence.get("where") or ""),
                    }
                    if best_state != OBSERVED_CONTRADICTION:
                        best_state = APPEARS_COMPLIANT
                    break
                elif local_state == NOT_OBSERVED_ON_THIS_EVIDENCE and best_state != OBSERVED_CONTRADICTION:
                    best_state = NOT_OBSERVED_ON_THIS_EVIDENCE

            req_record["pages"].append(page_record)
            if compliant_evidence and best_state != OBSERVED_CONTRADICTION:
                break

        req_record["candidate_findings"] = candidate_findings
        req_record["confirmed_evidence"] = compliant_evidence
        req_record["final_state"] = best_state
        diagnostics["results"].append(req_record)

        if best_state == OBSERVED_CONTRADICTION and candidate_findings:
            summary = "; ".join(dict.fromkeys(str(x.get("difference") or "") for x in candidate_findings if x.get("difference")))
            result.items.append(ComplianceItem(
                req,
                STATUS_NEEDS_CHECK,
                "наблюдается противоречие ПД↔РД; проверить evidence: " + (summary or "semantic contradiction"),
                evidence=summary,
                pages_to_check=[(str(x.get("path")), int(x.get("page") or 0)) for x in pages],
            ))
        elif compliant_evidence:
            result.items.append(ComplianceItem(
                req,
                STATUS_CONFIRMED,
                "требование наблюдается в локально проверенном evidence РД",
                evidence=str(compliant_evidence),
                pages_to_check=[(str(compliant_evidence["document"]), int(compliant_evidence["page"]))],
            ))
        elif had_error and not had_success:
            result.items.append(ComplianceItem(
                req, STATUS_NOT_CHECKED,
                "semantic verification технически не выполнена; отсутствие подтверждения не является расхождением",
            ))
            result.not_run.append(f"requirement {req_index}: all semantic calls failed")
        else:
            diagnostics["vision_unclear"] += 1
            detail = {
                NOT_OBSERVED_ON_THIS_EVIDENCE: "на проверенном evidence решение не наблюдается, но scope недостаточен для вывода об отсутствии",
                WRONG_OR_INSUFFICIENT_SCOPE: "доступный тип/масштаб evidence недостаточен для вывода",
                APPEARS_COMPLIANT: "whole-page выглядит согласованно, но локального доказательства недостаточно",
            }.get(best_state, "результат semantic verification остаётся неясным")
            result.items.append(ComplianceItem(
                req,
                STATUS_NEEDS_CHECK,
                detail,
                pages_to_check=[(str(x.get("path")), int(x.get("page") or 0)) for x in pages],
            ))

    from collections import Counter
    result.counts = dict(Counter(item.status for item in result.items))
    return result
