"""Evidence-first DRAWING PD -> DRAWING RD/ID semantic runtime.

Active flow:
    candidate pair -> whole-page inventory/scope -> region discovery
    -> local verification -> confirmed/candidate/unclear/no-change

Routing metadata chooses work only.  It never proves a difference and never
suppresses semantic comparison after a pair is selected.  A whole-page answer
is discovery evidence, not final graphical proof: every contradiction must be
locally re-observed before it can be confirmed.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Sequence

from .control_pair_candidates import candidate_pairs
from .llm import LlmConfig, call_llm_json
from .matching import DocumentInput
from .semantic_contract import (
    APPEARS_COMPLIANT,
    NOT_OBSERVED_ON_THIS_EVIDENCE,
    OBSERVED_CONTRADICTION,
    REGIONS_PER_CALL,
    WRONG_OR_INSUFFICIENT_SCOPE,
    finding_can_confirm,
    normalize_findings,
    normalize_inventory,
    normalize_regions,
    normalize_sheet_type,
    normalize_state,
    safe_no_change,
)
from .triangulation import Signal
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_GRAPHICAL_CANDIDATE_PAIRS = _int_env("NADZOR_MAX_GRAPHICAL_CANDIDATE_PAIRS", 18, 1, 60)
MAX_REGION_ZOOMS_PER_PAIR = _int_env("NADZOR_MAX_REGION_ZOOMS_PER_PAIR", 3, 1, 6)
MIN_LOCAL_CHECKS_FOR_NO_CHANGE = _int_env("NADZOR_MIN_LOCAL_CHECKS_FOR_NO_CHANGE", 2, 1, 4)

_PAIR_PROMPT = f"""Ты выполняешь blind-сравнение инженерного решения ПД и РД/ИД.
Работай только по наблюдаемым данным. Нельзя выводить нарушение из типовой
практики, норм, вероятных последствий или того, как система обычно устроена.

{UNTRUSTED_INPUT_RULE}

СНАЧАЛА ОПРЕДЕЛИ SCOPE:
- sheet_type каждой стороны: SCHEMATIC|PLAN|RCP|DETAIL|SCHEDULE|UNKNOWN;
- discipline/level/scale/orientation, только если реально читаются;
- достаточно ли именно этого типа evidence для проверяемого утверждения.
Разный жанр листа сам по себе НЕ расхождение. Схема и план могут описывать
одно решение разными способами.

СТАТУСЫ evidence_state:
- OBSERVED_CONTRADICTION — на обеих сторонах наблюдаются факты и между ними
  есть прямое противоречие;
- NOT_OBSERVED_ON_THIS_EVIDENCE — искомый элемент здесь не увиден, но это НЕ
  означает его отсутствия в РД;
- WRONG_OR_INSUFFICIENT_SCOPE — жанр/масштаб/читаемость/охват не позволяют
  делать вывод;
- APPEARS_COMPLIANT — проверенный scope согласуется, но только для реально
  просмотренного evidence.

КРИТИЧЕСКОЕ ПРАВИЛО ABSENCE:
Фраза «не вижу на этом листе» никогда не равна «отсутствует в РД».
Для presence_absence finding нужны одновременно: локально читаемая зона,
полный evidence_scope для данного утверждения и независимая corroboration
(например, другой вид/лист/спецификация/явная маркировка). Иначе возвращай
NOT_OBSERVED_ON_THIS_EVIDENCE либо candidate с requires_additional_evidence.

ПОРЯДОК:
1. Независимый inventory ПД.
2. Независимый inventory РД.
3. Scope/genre comparability.
4. Только затем direct contradiction.
5. Whole-page проход НЕ подтверждает finding окончательно. Он обязан вернуть
   до 3 действительно локальных candidate_regions для проверки спорных или
   инженерно насыщенных мест.

REGION RULES:
- максимум 3 зоны;
- bbox нормирован [x1,y1,x2,y2];
- bbox должен занимать примерно 2-40% страницы;
- зоны должны быть разными, без сильного перекрытия;
- [0,0,1,1] не является local zoom;
- если локализовать нельзя — state=WRONG_OR_INSUFFICIENT_SCOPE и объясни, какой
  дополнительный вид/evidence нужен.

Если 2 изображения: PD general, RD general.
Если 4 изображения: PD general, PD local (или general если PD bbox невозможно
сопоставить), RD general, RD local. В local режиме вывод делай прежде всего по
увеличенным зонам, а general используй для контекста.

Для finding обязательно:
- state=OBSERVED_CONTRADICTION;
- pd_claim, pd_evidence_ref;
- rd_claim, rd_evidence_ref;
- difference и difference_kind: presence_absence|configuration|connection|
  parameter|topology|quantity|location|coverage|other;
- evidence_scope: complete|partial|unknown;
- absence_verified=true только когда условия absence действительно выполнены;
- corroboration_refs — независимые подтверждения, если есть;
- requires_additional_evidence=true, если утверждение ещё нельзя считать
  доказанным.

Ответ только JSON:
{{
  "evidence_state":"OBSERVED_CONTRADICTION|NOT_OBSERVED_ON_THIS_EVIDENCE|WRONG_OR_INSUFFICIENT_SCOPE|APPEARS_COMPLIANT",
  "comparability":"high|medium|low",
  "pd_sheet":{{"sheet_type":"SCHEMATIC|PLAN|RCP|DETAIL|SCHEDULE|UNKNOWN","scale":"","level":"","orientation":""}},
  "rd_sheet":{{"sheet_type":"SCHEMATIC|PLAN|RCP|DETAIL|SCHEDULE|UNKNOWN","scale":"","level":"","orientation":""}},
  "pd_inventory":[{{"entity":"...","observation":"...","where":"..."}}],
  "rd_inventory":[{{"entity":"...","observation":"...","where":"..."}}],
  "findings":[{{
    "state":"OBSERVED_CONTRADICTION",
    "pd_claim":"...","pd_evidence_ref":"...",
    "rd_claim":"...","rd_evidence_ref":"...",
    "difference":"...","difference_kind":"configuration",
    "where":"...","evidence_scope":"complete|partial|unknown",
    "absence_verified":false,"corroboration_refs":[],
    "requires_additional_evidence":false
  }}],
  "candidate_regions":[{{"reason":"...","pd_bbox_norm":[0.1,0.1,0.4,0.4],"rd_bbox_norm":[0.1,0.1,0.4,0.4],"priority":"high|medium|low"}}],
  "coverage_notes":["..."],
  "uncertainties":["..."],
  "additional_evidence_needed":["..."]
}}"""

_DISCOVERY_NOTE = """
РЕЖИМ REGION DISCOVERY. Не завершай пару выводом «различий нет». Выбери до 3
разных локальных инженерно насыщенных зон (2-40% страницы каждая), которые
лучше всего проверят inventory и возможные тонкие изменения. Whole-page bbox
запрещён. Если scope/жанр не позволяет корректно локализовать сравнение —
верни WRONG_OR_INSUFFICIENT_SCOPE и additional_evidence_needed.
"""

_LOCAL_NOTE = """
РЕЖИМ LOCAL VERIFICATION. Перепроверь конкретную увеличенную область. Не
повторяй finding из whole-page по инерции: он подтверждается только если
противоречие реально видно в local evidence. NOT_OBSERVED не повышай до
absence. Для presence_absence укажи, достаточен ли evidence_scope и есть ли
independent corroboration.
"""


def _page_text(document: DocumentInput, page: int) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in document.text_facts
        if int(item.get("page") or 0) == page
    )


def _comparability(result: dict) -> str:
    value = str(result.get("comparability") or "").strip().casefold()
    return value if value in {"high", "medium", "low"} else "low"


def _sheet_meta(value: object) -> dict:
    row = value if isinstance(value, dict) else {}
    return {
        "sheet_type": normalize_sheet_type(row.get("sheet_type")),
        "scale": str(row.get("scale") or "").strip(),
        "level": str(row.get("level") or "").strip(),
        "orientation": str(row.get("orientation") or "").strip(),
    }


def _dedupe(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("pd_claim") or "").casefold(),
            str(item.get("rd_claim") or "").casefold(),
            str(item.get("difference") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _merge_regions(existing: list[dict], new: list[dict]) -> list[dict]:
    # Re-normalize the full set so overlap rules apply across both calls.
    raw = [
        {k: v for k, v in item.items() if k in {"reason", "pd_bbox_norm", "rd_bbox_norm", "priority"}}
        for item in [*existing, *new]
    ]
    return normalize_regions(raw, limit=REGIONS_PER_CALL)


@dataclass
class _State:
    pair: object
    before_path: str
    after_path: str
    before_doc: DocumentInput
    after_doc: DocumentInput
    base: dict
    whole_page_done: bool = False
    region_discovery_done: bool = False
    semantic_state: str = WRONG_OR_INSUFFICIENT_SCOPE
    comparability: str = "low"
    pd_sheet: dict = field(default_factory=dict)
    rd_sheet: dict = field(default_factory=dict)
    pd_inventory: list[dict] = field(default_factory=list)
    rd_inventory: list[dict] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    checked_regions: list[dict] = field(default_factory=list)
    confirmed: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    coverage_notes: list[str] = field(default_factory=list)
    additional_evidence_needed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def regions_seen(self) -> int:
        return len(self.checked_regions)

    def status(self) -> str:
        if self.confirmed:
            return "confirmed_difference"
        if self.candidates:
            return "candidate_difference_unverified"
        if self.errors or not self.whole_page_done:
            return "technical_incomplete"
        if self.semantic_state == WRONG_OR_INSUFFICIENT_SCOPE:
            return "wrong_or_insufficient_scope"
        if self.semantic_state == NOT_OBSERVED_ON_THIS_EVIDENCE:
            return "not_observed_on_this_evidence"
        required = min(MIN_LOCAL_CHECKS_FOR_NO_CHANGE, MAX_REGION_ZOOMS_PER_PAIR)
        pending_high = any(r.get("priority") == "high" for r in self.regions[self.regions_seen:])
        if safe_no_change(
            state=self.semantic_state,
            comparability=self.comparability,
            pd_inventory=self.pd_inventory,
            rd_inventory=self.rd_inventory,
            checked_regions=self.regions_seen,
            required_regions=required,
            pending_high_regions=pending_high,
            errors=self.errors,
            cache_only=False,
        ):
            return "compared_no_candidate"
        return "unclear"


def _call_model(
    state: _State,
    config: LlmConfig,
    region: dict | None = None,
    *,
    discover_regions: bool = False,
) -> dict:
    pair = state.pair
    pd_general = render_page_to_data_url(state.before_path, pair.before_page)
    rd_general = render_page_to_data_url(state.after_path, pair.after_page)
    images = [pd_general, rd_general]
    mode_note = ""
    if region is not None:
        pd_clip = region.get("pd_bbox_norm")
        rd_clip = region.get("rd_bbox_norm")
        pd_local = (
            render_page_to_data_url(state.before_path, pair.before_page, clip_frac=pd_clip)
            if pd_clip is not None else pd_general
        )
        rd_local = render_page_to_data_url(state.after_path, pair.after_page, clip_frac=rd_clip)
        images = [pd_general, pd_local, rd_general, rd_local]
        mode_note = (
            f"\nПроверяемая local region: reason={region.get('reason')!r}; "
            f"pd_bbox={pd_clip}; rd_bbox={rd_clip}.\n{_LOCAL_NOTE}"
        )
    elif discover_regions:
        mode_note = _DISCOVERY_NOTE

    pd_text = _page_text(state.before_doc, pair.before_page)[:3000]
    rd_text = _page_text(state.after_doc, pair.after_page)[:3000]
    user_text = (
        "Пара уже выбрана routing-слоем. Routing score/rooms/anchors не являются evidence.\n"
        f"ПД text layer: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{pd_text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"РД text layer: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{rd_text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"{mode_note}"
    )
    digest = hashlib.sha256(
        f"{state.before_path}:{pair.before_page}:{state.after_path}:{pair.after_page}:"
        f"{region}:{discover_regions}:semantic-pair-v4".encode()
    ).hexdigest()
    result = call_llm_json(
        config,
        _PAIR_PROMPT,
        user_text,
        images=images,
        operation="vision",
        source_digest=digest,
        prompt_version="semantic-pair-v4-scope-local-contract",
    )
    return result if isinstance(result, dict) else {}


def _append_texts(target: list[str], value: object) -> None:
    for item in value if isinstance(value, list) else []:
        text = str(item).strip()
        if text and text not in target:
            target.append(text)


def _apply_result(state: _State, result: dict, *, whole_page: bool, region: dict | None = None) -> None:
    comp = _comparability(result)
    semantic_state = normalize_state(result.get("evidence_state") or result.get("state"))
    findings = normalize_findings(result.get("findings") or result.get("differences"))

    if whole_page:
        state.whole_page_done = True
        state.comparability = comp
        state.semantic_state = semantic_state
        state.pd_sheet = _sheet_meta(result.get("pd_sheet"))
        state.rd_sheet = _sheet_meta(result.get("rd_sheet"))
        state.pd_inventory = normalize_inventory(result.get("pd_inventory"))
        state.rd_inventory = normalize_inventory(result.get("rd_inventory"))
        state.regions = normalize_regions(result.get("candidate_regions"))
        # Whole-page contradictions are hypotheses only; local re-observation
        # is mandatory before graphical confirmation.
        state.candidates.extend(findings)
    else:
        if comp == "high" or (comp == "medium" and state.comparability == "low"):
            state.comparability = comp
        if semantic_state == OBSERVED_CONTRADICTION:
            state.semantic_state = OBSERVED_CONTRADICTION
        elif state.semantic_state != OBSERVED_CONTRADICTION:
            state.semantic_state = semantic_state

        for finding in findings:
            if finding_can_confirm(
                finding,
                comparability=comp,
                local_verified=True,
                scope_state=semantic_state,
            ):
                state.confirmed.append(finding)
            else:
                state.candidates.append(finding)
        state.checked_regions.append({
            "reason": region.get("reason") if region else "",
            "pd_bbox_norm": region.get("pd_bbox_norm") if region else None,
            "rd_bbox_norm": region.get("rd_bbox_norm") if region else None,
            "comparability": comp,
            "evidence_state": semantic_state,
            "findings": findings,
            "uncertainties": [str(x).strip() for x in (result.get("uncertainties") or []) if str(x).strip()],
        })

    _append_texts(state.uncertainties, result.get("uncertainties"))
    _append_texts(state.coverage_notes, result.get("coverage_notes"))
    _append_texts(state.additional_evidence_needed, result.get("additional_evidence_needed"))


def run_targeted_pair_vision(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_pairs: int = 6,
):
    """Run the active graphical semantic path and preserve the old Signal API."""
    signals: list[Signal] = []
    diagnostics: list[dict] = []
    if max_pairs <= 0:
        return signals, diagnostics

    all_pairs = candidate_pairs(before_docs, after_docs)
    pairs = all_pairs[: min(max_pairs, MAX_GRAPHICAL_CANDIDATE_PAIRS)]
    states: list[_State] = []

    # Pass 1: whole-page discovery for every selected pair before any zoom.
    for pair in pairs:
        state = _State(
            pair=pair,
            before_path=before_paths[pair.before_file_idx],
            after_path=after_paths[pair.after_file_idx],
            before_doc=before_docs[pair.before_file_idx],
            after_doc=after_docs[pair.after_file_idx],
            base={
                "control_type": "page_pair",
                "pair_key": pair.key,
                "before_page": pair.before_page,
                "after_page": pair.after_page,
                "before_file_index": pair.before_file_idx,
                "after_file_index": pair.after_file_idx,
                "matched_by": pair.matched_by,
                "routing_score": round(pair.score, 6),
                "rooms_shared": list(pair.shared_rooms),
                "anchors_shared": list(getattr(pair, "shared_anchors", ()) or ()),
            },
        )
        states.append(state)
        try:
            _apply_result(state, _call_model(state, config), whole_page=True)
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"whole-page vision failed: {type(exc).__name__}: {exc}")

    # Pass 1b: insufficient/duplicate/whole-page regions are rejected by the
    # universal contract, then the model gets one explicit discovery request.
    required = min(MIN_LOCAL_CHECKS_FOR_NO_CHANGE, MAX_REGION_ZOOMS_PER_PAIR)
    for state in states:
        if state.errors or not state.whole_page_done or len(state.regions) >= required:
            continue
        try:
            discovered = _call_model(state, config, discover_regions=True)
            state.region_discovery_done = True
            state.regions = _merge_regions(
                state.regions, normalize_regions(discovered.get("candidate_regions"))
            )
            _append_texts(state.uncertainties, discovered.get("uncertainties"))
            _append_texts(state.additional_evidence_needed, discovered.get("additional_evidence_needed"))
            if normalize_state(discovered.get("evidence_state") or discovered.get("state")) == WRONG_OR_INSUFFICIENT_SCOPE:
                state.semantic_state = WRONG_OR_INSUFFICIENT_SCOPE
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"region discovery failed: {type(exc).__name__}: {exc}")

    # Pass 2: fair round-robin local verification.  Whole-page candidates do
    # not short-circuit this stage.
    for round_index in range(MAX_REGION_ZOOMS_PER_PAIR):
        for state in states:
            if round_index >= len(state.regions):
                continue
            region = state.regions[round_index]
            try:
                result = _call_model(state, config, region)
                _apply_result(state, result, whole_page=False, region=region)
            except Exception as exc:  # noqa: BLE001
                state.errors.append(
                    f"zoom {round_index + 1} failed: {type(exc).__name__}: {exc}"
                )

    for state in states:
        state.confirmed = _dedupe(state.confirmed)
        state.candidates = _dedupe(state.candidates)
        # A candidate identical to a locally confirmed finding is no longer
        # unverified.
        confirmed_keys = {
            (x.get("pd_claim"), x.get("rd_claim"), x.get("difference")) for x in state.confirmed
        }
        state.candidates = [
            x for x in state.candidates
            if (x.get("pd_claim"), x.get("rd_claim"), x.get("difference")) not in confirmed_keys
        ]
        status = state.status()
        diagnostics.append({
            **state.base,
            "status": status,
            "whole_page_done": state.whole_page_done,
            "region_discovery_done": state.region_discovery_done,
            "evidence_state": state.semantic_state,
            "comparability": state.comparability,
            "pd_sheet": state.pd_sheet,
            "rd_sheet": state.rd_sheet,
            "pd_inventory": state.pd_inventory,
            "rd_inventory": state.rd_inventory,
            "candidate_regions": state.regions,
            "candidate_regions_total": len(state.regions),
            "candidate_regions_checked": state.regions_seen,
            "checked_regions": state.checked_regions,
            "min_local_checks_for_no_change": MIN_LOCAL_CHECKS_FOR_NO_CHANGE,
            "confirmed_findings": state.confirmed,
            "unverified_candidates": state.candidates,
            "coverage_notes": state.coverage_notes,
            "uncertainties": state.uncertainties,
            "additional_evidence_needed": state.additional_evidence_needed,
            "errors": state.errors,
        })
        if state.confirmed:
            detail = " | ".join(item["difference"] for item in state.confirmed[:8])
            signals.append(Signal("vision_pair", "page_pair", state.pair.key, detail))

    diagnostics.append({
        "control_type": "coverage",
        "status": "complete" if len(all_pairs) <= len(pairs) else "candidate_budget",
        "candidate_pairs_total": len(all_pairs),
        "candidate_pairs_checked": len(pairs),
        "semantic_architecture": "pair_discovery->scope_inventory->region_discovery->local_verification->universal_evidence_contract",
        "legacy_room_runtime_active": False,
        "legacy_generic_region_runtime_active": False,
        "raster_gate_active": False,
        "triangulation_required_for_vision": False,
        "whole_page_finding_final": False,
        "whole_page_no_change_allowed": False,
        "region_limit": REGIONS_PER_CALL,
        "min_local_checks_for_no_change": MIN_LOCAL_CHECKS_FOR_NO_CHANGE,
    })
    return signals, diagnostics
