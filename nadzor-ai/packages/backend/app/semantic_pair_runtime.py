"""Clean evidence-first drawing comparison runtime.

Active flow:

    candidate pair
      -> whole-page inventory of PD and RD
      -> evidence comparison
      -> region discovery when coverage is not yet sufficient
      -> model-driven local zoom
      -> finding / unclear / compared-no-candidate

The module intentionally does not import legacy room/non-room/raster
orchestrators. Pair metadata may rank work, but once a pair is selected it
cannot suppress semantic comparison. A finding is accepted only when the model
gives explicit observations for BOTH PD and RD and states a direct
contradiction. A clean/no-change result is allowed only after local coverage;
a single whole-page glance at a dense engineering sheet is never enough.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Sequence

from .control_pair_candidates import candidate_pairs
from .llm import LlmConfig, call_llm_json
from .matching import DocumentInput
from .triangulation import Signal
from .vision import UNTRUSTED_INPUT_RULE, render_page_to_data_url


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


MAX_GRAPHICAL_CANDIDATE_PAIRS = _int_env("NADZOR_MAX_GRAPHICAL_CANDIDATE_PAIRS", 18, 1, 60)
MAX_REGION_ZOOMS_PER_PAIR = _int_env("NADZOR_MAX_REGION_ZOOMS_PER_PAIR", 3, 0, 6)
MIN_LOCAL_CHECKS_FOR_NO_CHANGE = _int_env("NADZOR_MIN_LOCAL_CHECKS_FOR_NO_CHANGE", 2, 1, 4)
MAX_DISCOVERED_REGIONS = _int_env("NADZOR_MAX_DISCOVERED_REGIONS", 4, 2, 6)

_PAIR_PROMPT = f"""Ты выполняешь blind-сравнение инженерных решений ПД и РД/ИД.
Нужно находить только НАБЛЮДАЕМЫЕ расхождения. Нельзя выводить нарушение из
общего инженерного знания, типовой практики, нормативных ожиданий, вероятных
последствий или предположений о том, как система 'должна' быть устроена.

{UNTRUSTED_INPUT_RULE}

КРИТИЧЕСКОЕ ПРАВИЛО ДОКАЗАТЕЛЬНОСТИ:
Finding допустим только если одновременно есть:
1) конкретно наблюдаемое проектное решение на ПД;
2) конкретно наблюдаемый факт на РД/ИД;
3) прямое противоречие между 1 и 2.
Если любой из трёх пунктов не наблюдается — не придумывай. Верни uncertainty
и candidate_region для увеличения.

Запрещено считать доказательством формулировки вроде: 'вероятно', 'обычно',
'это подразумевает', 'должно быть', 'может привести', 'типично для'. Не делай
legal/severity conclusions и не объясняй возможные причины изменения.

ПОРЯДОК АНАЛИЗА ОБЯЗАТЕЛЕН:
A. Сначала независимо опиши инженерный inventory ПД.
B. Затем независимо опиши инженерный inventory РД.
C. Только затем сопоставь элементы, связи, ветви, трассы, параметры и
   конфигурацию и сформулируй прямые противоречия.
D. Если whole-page просмотр не дал finding, это НЕ финальный вывод. Для
   плотного инженерного листа предложи 2-4 наиболее информативные зоны для
   локальной проверки. Предпочитай зоны с высокой инженерной насыщенностью,
   узлами соединений, установками, коллекторами, разветвлениями и группами
   терминалов. Не выбирай штамп/экспликацию, если инженерная графика есть.

Сравнивай инженерную суть: наличие/отсутствие элементов, состав оборудования,
количество, маркировки, параметры, подключения, ветви, трассы и topology.
Разный жанр листа (схема/план) сам по себе не является отличием.

Если даны 2 изображения: первое ПД, второе РД/ИД.
Если даны 4 изображения: PD general, PD local, RD general, RD local.

Для каждого finding верни evidence contract:
- pd_claim: что именно установлено по ПД;
- pd_evidence: что конкретно видно/читается на ПД;
- rd_claim: что именно установлено по РД;
- rd_evidence: что конкретно видно/читается на РД;
- difference: прямое противоречие;
- where: помещение/ось/марка/зона, если читается.

comparability:
- high: обе стороны достаточно читаемы для данного вывода;
- medium: инженерная графика есть, но часть деталей неразборчива;
- low: нужная инженерная информация не видна.

candidate_regions: для drawing->drawing желательно дать bbox обеих сторон.
Координаты нормированы [x1,y1,x2,y2] от 0 до 1.

Ответ только JSON:
{{
  "comparability":"high|medium|low",
  "pd_inventory":[{{"entity":"...","observation":"...","where":"..."}}],
  "rd_inventory":[{{"entity":"...","observation":"...","where":"..."}}],
  "findings":[{{
    "pd_claim":"...",
    "pd_evidence":"...",
    "rd_claim":"...",
    "rd_evidence":"...",
    "difference":"...",
    "where":"..."
  }}],
  "candidate_regions":[{{
    "reason":"...",
    "pd_bbox_norm":[0.0,0.0,1.0,1.0],
    "rd_bbox_norm":[0.0,0.0,1.0,1.0],
    "priority":"high|medium|low"
  }}],
  "coverage_notes":["..."],
  "uncertainties":["..."]
}}"""

_REGION_DISCOVERY_INSTRUCTION = """\nРЕЖИМ REGION DISCOVERY. На whole-page проходе не получено достаточного локального покрытия.
Не пытайся закончить сравнением 'различий нет'. Выбери от 2 до 4 разных
инженерно насыщенных зон, которые надо увеличить, чтобы проверить тонкие
изменения конфигурации/наличия/ветвления. Верни их в candidate_regions.
Если корректно сопоставить зоны между ПД и РД невозможно — всё равно назови
RD bbox и объясни uncertainty; не выдумывай finding."""


def _page_text(document: DocumentInput, page: int) -> str:
    return "\n".join(
        str(item.get("text") or "")
        for item in document.text_facts
        if int(item.get("page") or 0) == page
    )


def _comparability(result: dict) -> str:
    value = str(result.get("comparability") or "").strip().casefold()
    return value if value in {"high", "medium", "low"} else "low"


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _regions(value: object, limit: int = MAX_DISCOVERED_REGIONS) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        rd_box = _bbox(item.get("rd_bbox_norm") or item.get("bbox_norm"))
        if rd_box is None:
            continue
        pd_box = _bbox(item.get("pd_bbox_norm"))
        priority = str(item.get("priority") or "medium").strip().casefold()
        out.append({
            "reason": str(item.get("reason") or "").strip(),
            "pd_bbox_norm": pd_box,
            "rd_bbox_norm": rd_box,
            "priority": priority if priority in {"high", "medium", "low"} else "medium",
        })
        if len(out) >= limit:
            break
    return out


def _inventory(value: object, limit: int = 80) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, dict):
            entity = str(item.get("entity") or "").strip()
            observation = str(item.get("observation") or "").strip()
            where = str(item.get("where") or "").strip()
            if entity or observation:
                out.append({"entity": entity, "observation": observation, "where": where})
        elif str(item).strip():
            out.append({"entity": "", "observation": str(item).strip(), "where": ""})
        if len(out) >= limit:
            break
    return out


def _grounded_findings(result: dict) -> list[dict]:
    """Keep only findings with explicit evidence on both sides."""
    raw = result.get("findings")
    if not isinstance(raw, list):
        raw = result.get("differences")
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        pd_claim = str(item.get("pd_claim") or item.get("pd_observation") or "").strip()
        pd_evidence = str(item.get("pd_evidence") or item.get("pd_observation") or "").strip()
        rd_claim = str(item.get("rd_claim") or item.get("rd_observation") or "").strip()
        rd_evidence = str(item.get("rd_evidence") or item.get("rd_observation") or "").strip()
        difference = str(item.get("difference") or item.get("change") or "").strip()
        if not all((pd_claim, pd_evidence, rd_claim, rd_evidence, difference)):
            continue
        out.append({
            "pd_claim": pd_claim,
            "pd_evidence": pd_evidence,
            "rd_claim": rd_claim,
            "rd_evidence": rd_evidence,
            "difference": difference,
            "where": str(item.get("where") or "").strip(),
        })
    return out


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
    comparability: str = "low"
    regions: list[dict] = field(default_factory=list)
    regions_seen: int = 0
    pd_inventory: list[dict] = field(default_factory=list)
    rd_inventory: list[dict] = field(default_factory=list)
    confirmed: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    coverage_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def local_coverage_sufficient(self) -> bool:
        required = min(MIN_LOCAL_CHECKS_FOR_NO_CHANGE, MAX_REGION_ZOOMS_PER_PAIR)
        return required > 0 and self.regions_seen >= required

    def status(self) -> str:
        if self.confirmed:
            return "confirmed_difference"
        if self.candidates:
            return "candidate_difference_unverified"
        if not self.whole_page_done or self.errors:
            return "technical_incomplete"
        if self.comparability != "high":
            return "unclear"
        if not self.pd_inventory or not self.rd_inventory:
            return "unclear"
        if not self.local_coverage_sufficient():
            return "unclear"
        if any(r["priority"] == "high" for r in self.regions[self.regions_seen:]):
            return "technical_incomplete"
        return "compared_no_candidate"


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
    region_note = ""
    if region is not None:
        pd_clip = region.get("pd_bbox_norm")
        rd_clip = region.get("rd_bbox_norm")
        pd_local = (
            render_page_to_data_url(state.before_path, pair.before_page, clip_frac=pd_clip)
            if pd_clip is not None else pd_general
        )
        rd_local = render_page_to_data_url(state.after_path, pair.after_page, clip_frac=rd_clip)
        images = [pd_general, pd_local, rd_general, rd_local]
        region_note = f"\nПроверяемая зона: {region.get('reason') or 'model proposed zoom'}."

    pd_text = _page_text(state.before_doc, pair.before_page)[:3000]
    rd_text = _page_text(state.after_doc, pair.after_page)[:3000]
    user_text = (
        "Эта пара уже выбрана routing-слоем. Не решай заново, являются ли листы парой. "
        "Routing score и anchors не являются evidence.\n"
        f"ПД text layer: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{pd_text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>\n"
        f"РД text layer: <НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>{rd_text}</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>"
        f"{region_note}"
        f"{_REGION_DISCOVERY_INSTRUCTION if discover_regions else ''}"
    )
    digest = hashlib.sha256(
        f"{state.before_path}:{pair.before_page}:{state.after_path}:{pair.after_page}:"
        f"{region}:{discover_regions}".encode()
    ).hexdigest()
    result = call_llm_json(
        config,
        _PAIR_PROMPT,
        user_text,
        images=images,
        operation="vision",
        source_digest=digest,
        prompt_version="semantic-pair-v3-inventory-local-coverage",
    )
    return result if isinstance(result, dict) else {}


def _merge_regions(existing: list[dict], new: list[dict]) -> list[dict]:
    out = list(existing)
    seen = {(r.get("pd_bbox_norm"), r.get("rd_bbox_norm")) for r in out}
    for region in new:
        key = (region.get("pd_bbox_norm"), region.get("rd_bbox_norm"))
        if key in seen:
            continue
        seen.add(key)
        out.append(region)
        if len(out) >= MAX_DISCOVERED_REGIONS:
            break
    return out


def _apply_result(state: _State, result: dict, *, whole_page: bool) -> None:
    comp = _comparability(result)
    if whole_page:
        state.whole_page_done = True
        state.comparability = comp
        state.regions = _regions(result.get("candidate_regions"))
        state.pd_inventory = _inventory(result.get("pd_inventory"))
        state.rd_inventory = _inventory(result.get("rd_inventory"))
    elif comp == "high" or (comp == "medium" and state.comparability == "low"):
        state.comparability = comp

    findings = _grounded_findings(result)
    if comp == "high":
        state.confirmed.extend(findings)
    else:
        state.candidates.extend(findings)
    state.uncertainties.extend(
        str(x).strip() for x in (result.get("uncertainties") or [])
        if str(x).strip()
    )
    state.coverage_notes.extend(
        str(x).strip() for x in (result.get("coverage_notes") or [])
        if str(x).strip()
    )


def run_targeted_pair_vision(
    before_docs: Sequence[DocumentInput],
    after_docs: Sequence[DocumentInput],
    before_paths: Sequence[str],
    after_paths: Sequence[str],
    config: LlmConfig,
    *,
    max_pairs: int = 6,
):
    """Run the clean semantic path and adapt confirmed findings to old Signal API."""
    signals: list[Signal] = []
    diagnostics: list[dict] = []
    if max_pairs <= 0:
        return signals, diagnostics

    all_pairs = candidate_pairs(before_docs, after_docs)
    pairs = all_pairs[: min(max_pairs, MAX_GRAPHICAL_CANDIDATE_PAIRS)]
    states: list[_State] = []

    # Pass 1: every selected pair is inspected whole-page before any zoom.
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
        except Exception as exc:
            state.errors.append(f"whole-page vision failed: {type(exc).__name__}: {exc}")

    # Pass 1b: a clean whole-page result must produce enough local regions.
    # If it did not, ask the same model explicitly for region discovery.
    for state in states:
        if state.confirmed or state.errors or not state.whole_page_done:
            continue
        needed = min(MIN_LOCAL_CHECKS_FOR_NO_CHANGE, MAX_REGION_ZOOMS_PER_PAIR)
        if len(state.regions) >= needed:
            continue
        try:
            discovered = _call_model(state, config, discover_regions=True)
            state.region_discovery_done = True
            state.regions = _merge_regions(
                state.regions, _regions(discovered.get("candidate_regions"))
            )
            state.uncertainties.extend(
                str(x).strip() for x in (discovered.get("uncertainties") or [])
                if str(x).strip()
            )
        except Exception as exc:
            state.errors.append(f"region discovery failed: {type(exc).__name__}: {exc}")

    # Pass 2: model-requested zoom, fair round-robin across pairs.
    for round_index in range(MAX_REGION_ZOOMS_PER_PAIR):
        for state in states:
            if state.confirmed or round_index >= len(state.regions):
                continue
            region = state.regions[round_index]
            try:
                result = _call_model(state, config, region)
                _apply_result(state, result, whole_page=False)
                state.regions_seen = round_index + 1
            except Exception as exc:
                state.errors.append(
                    f"zoom {round_index + 1} failed: {type(exc).__name__}: {exc}"
                )

    for state in states:
        state.confirmed = _dedupe(state.confirmed)
        state.candidates = _dedupe(state.candidates)
        status = state.status()
        record = {
            **state.base,
            "status": status,
            "whole_page_done": state.whole_page_done,
            "region_discovery_done": state.region_discovery_done,
            "comparability": state.comparability,
            "pd_inventory": state.pd_inventory,
            "rd_inventory": state.rd_inventory,
            "candidate_regions_total": len(state.regions),
            "candidate_regions_checked": state.regions_seen,
            "min_local_checks_for_no_change": MIN_LOCAL_CHECKS_FOR_NO_CHANGE,
            "confirmed_findings": state.confirmed,
            "unverified_candidates": state.candidates,
            "coverage_notes": state.coverage_notes,
            "uncertainties": state.uncertainties,
            "errors": state.errors,
        }
        if state.confirmed:
            detail = " | ".join(item["difference"] for item in state.confirmed[:8])
            signals.append(Signal("vision_pair", "page_pair", state.pair.key, detail))
        diagnostics.append(record)

    diagnostics.append({
        "control_type": "coverage",
        "status": "complete" if len(all_pairs) <= len(pairs) else "candidate_budget",
        "candidate_pairs_total": len(all_pairs),
        "candidate_pairs_checked": len(pairs),
        "semantic_architecture": "pair_discovery->inventory->region_discovery->local_zoom->evidence_contract",
        "legacy_room_runtime_active": False,
        "legacy_generic_region_runtime_active": False,
        "raster_gate_active": False,
        "triangulation_required_for_vision": False,
        "whole_page_no_change_allowed": False,
        "min_local_checks_for_no_change": MIN_LOCAL_CHECKS_FOR_NO_CHANGE,
    })
    return signals, diagnostics
