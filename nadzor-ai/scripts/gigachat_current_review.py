"""Blind GigaChat review for the current lean NADZOR.AI architecture.

This wrapper reuses the existing authenticated peer-review runner, but replaces
its stale architecture prompt and runtime summary with the current semantic
runtime. It never receives benchmark ground truth, expected rooms/sheets or
known violations.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gigachat_peer_review as base


RUNTIME_SYSTEM = """Ты — независимый technical peer reviewer runtime системы
НАДЗОР.ИИ. Анализируй только execution-диагностику. Тебе не даны benchmark
ground truth, expected rooms/sheets или known violations. Не пытайся угадывать
их. Technical failure, cache-only run, partial coverage и отсутствие реального
local zoom нельзя трактовать как отсутствие расхождений. Верни только JSON."""

ARCHITECTURE_SYSTEM = """Ты — независимый reviewer архитектуры НАДЗОР.ИИ и
эксперт именно по тому, как GigaChat-3-Ultra лучше читает инженерные чертежи и
текстовые требования. Рекомендации должны быть model-specific и blind.
Navigation/routing evidence может только выбирать, что смотреть; оно не
является доказательством finding. Верни только JSON."""

CURRENT_ARCHITECTURE = """CURRENT IMPLEMENTATION:
1. lean_analysis_runtime имеет два semantic track: TEXT requirement ПД ->
   relevant RD evidence -> отдельный compliance/requirement-on-page Vision;
   DRAWING ПД -> candidate drawing РД -> semantic_pair_runtime.
2. DRAWING->DRAWING уже evidence-first: candidate pair -> whole-page PD/RD ->
   independent inventories -> direct contradiction check -> region discovery ->
   local zoom -> evidence contract. Старые room/non-room/raster orchestrators
   не являются active semantic gate.
3. Finding принимается только с pd_claim + pd_evidence + rd_claim + rd_evidence
   + direct difference. No-change разрешён только при high comparability,
   непустом inventory обеих сторон, достаточном local coverage, отсутствии
   errors и непроверенных high-priority regions.
4. Если whole-page не дал finding, GigaChat отдельно вызывается для region
   discovery. Local call получает PD general + PD local + RD general + RD local.
   Но bbox может оказаться почти whole-page; если координаты не логируются, это
   observability gap, а не доказанная локальная проверка.
5. TEXT->DRAWING пока отдельный semantic path и не использует тот же runtime.
6. Legacy room/equipment registries, composition, routing_diff, verdict
   synthesis и mandatory triangulation в lean runtime inactive.
7. Persistent LLM result cache означает: requests=0 при persistent_cache_hits>0
   не является fresh проверкой текущего prompt/model behavior. Для blind
   benchmark LLM-result cache должен быть fresh, deterministic PDF/facts/file
   cache можно сохранять.
8. Главный общий риск: "не вижу элемент на данном листе/жанре РД" НЕ означает
   "элемент отсутствует в РД". Особенно опасно schematic<->plan и неполное
   evidence set. Нужен evidence-scope state, а не hardcoded hints.
"""

RUNTIME_PROMPT = """Проведи review ACTUAL_RUNTIME_SUMMARY строго по логам.
Проверь:
1. fresh это run, cache_only, mixed или unknown. requests=0 при
   persistent_cache_hits/result_cache_hits>0 = не fresh;
2. какие semantic stages реально выполнены и где technical/coverage gaps;
3. requirement->drawing: доказаны ли Vision и настоящие local zoom; если bbox
   coordinates отсутствуют, отметь observability gap;
4. drawing->drawing: whole-page, inventories, region discovery, local checks,
   comparability, confirmed/unverified/unclear/no-candidate;
5. можно ли доверять no-candidate или coverage недостаточно;
6. есть ли риск inference "не наблюдается на evidence" => "отсутствует";
7. создаёт ли отдельный legacy analysis run findings вне lean runtime;
8. какие конкретные поля надо логировать на следующем blind run.

Верни JSON:
{
  "freshness":{"classification":"fresh|cache_only|mixed|unknown","evidence":[],"can_assess_current_prompt_behavior":true},
  "execution":{"completed_stages":[],"technical_gaps":[],"coverage_gaps":[]},
  "requirement_to_drawing":{"assessment":"...","vision_proved":false,"local_zoom_proved":false,"missing_observability":[]},
  "drawing_to_drawing":{"assessment":"...","whole_page_proved":false,"local_checks_proved":false,"no_candidate_trust":"low|medium|high|unknown","missing_observability":[]},
  "absence_vs_not_observed_risk":{"level":"low|medium|high|unknown","evidence":[],"needed_guardrails":[]},
  "legacy_noise":[],
  "logging_changes":[],
  "priority_runtime_changes":[]
}
"""

ARCHITECTURE_PROMPT = """Проведи model-specific review CURRENT_ARCHITECTURE.
Нужна максимально простая и стабильная механика для GigaChat-3-Ultra.

Ответь:
1. Какой state/JSON contract лучше, чтобы строго различать OBSERVED
   CONTRADICTION, NOT OBSERVED ON THIS EVIDENCE, WRONG/INSUFFICIENT SCOPE и
   APPEARS COMPLIANT? Как запретить превращать not-observed в absent?
2. Для schematic<->plan какие preconditions нужны до вывода об отсутствии?
   Нужно ли сначала определить, обязан ли объект присутствовать в этом жанре?
3. Как делать region discovery, чтобы bbox был реально локальным, а не
   [0,0,1,1]? Дай ограничения на площадь, overlap, число regions и fallback.
4. Сколько local regions обычно достаточно и когда расширять coverage?
5. Стоит ли объединить TEXT->DRAWING и DRAWING->DRAWING в один universal
   semantic engine? Дай минимальный общий contract; у text requirement PD image
   может отсутствовать.
6. Какие evidence fields нужны, чтобы finding = PROJECT INTENT + RD OBSERVATION
   + DIRECT CONTRADICTION, а не инженерная догадка?
7. Какие условия разрешают appears_compliant/no-change и какие всегда блокируют
   его: provider error, cache-only evaluation, budget exhaustion, partial
   evidence, wrong genre, unreadable labels и т.д.?
8. Как искать unknown изменения без room/axis/equipment hard gates?
9. Что можно удалить как overengineering без потери recall?
10. Дай конкретные prompt rules для GigaChat-3-Ultra против hallucinated
    absence: когда просить additional evidence/zoom вместо finding.
11. Какой cache policy нужен для blind benchmark: что должно быть fresh и какие
    deterministic caches безопасно оставлять?
12. TOP-5 изменений. Никаких советов под конкретные известные листы/помещения.

Верни JSON:
{
  "state_model":{"statuses":[],"absence_rule":"...","wrong_scope_rule":"..."},
  "schematic_vs_plan":{"preconditions":[],"comparison_order":[],"forbidden_inferences":[]},
  "region_discovery":{"regions_per_call":0,"bbox_area_min":0.0,"bbox_area_max":0.0,"max_overlap":0.0,"fallback_when_unlocalizable":"..."},
  "universal_contract":{},
  "safe_no_change":{"allowed_when":[],"blocked_when":[]},
  "unknown_change_strategy":[],
  "simplify_or_remove":[],
  "gigachat_prompt_rules":[],
  "blind_benchmark_cache_policy":{"must_be_fresh":[],"safe_to_keep":[]},
  "top_5_changes":[]
}
"""


def _metrics(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    keys = (
        "requests", "responses", "errors", "invalid_results", "retries",
        "rate_limits", "image_uploads", "image_cache_hits", "result_cache_hits",
        "persistent_cache_hits", "provider_queue_events", "provider_slot_acquired",
        "provider_queue_timeouts", "provider_queue_wait_seconds",
        "mean_provider_queue_wait_seconds", "mean_response_seconds",
        "elapsed_seconds",
    )
    return {key: raw.get(key) for key in keys if key in raw}


def _short(value: object, limit: int = 300) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _compact_pair_vision(block: object, limit: int = 16) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    out = {key: block.get(key) for key in ("counts", "coverage") if key in block}
    rows = []
    for item in block.get("results") or []:
        if not isinstance(item, dict):
            continue
        row = {
            key: item.get(key)
            for key in (
                "status", "pair_key", "before_page", "after_page",
                "before_file_index", "after_file_index", "routing_score",
                "comparability", "whole_page_done", "region_discovery_done",
                "candidate_regions_total", "candidate_regions_checked",
                "min_local_checks_for_no_change", "rooms_shared", "anchors_shared",
                "coverage_notes", "uncertainties", "errors",
            )
            if key in item
        }
        for inventory_key in ("pd_inventory", "rd_inventory"):
            values = []
            for entry in item.get(inventory_key) or []:
                if isinstance(entry, dict):
                    values.append({
                        "entity": _short(entry.get("entity"), 100),
                        "observation": _short(entry.get("observation"), 220),
                        "where": _short(entry.get("where"), 120),
                    })
                if len(values) >= 10:
                    break
            row[inventory_key] = values
        row["confirmed_findings"] = (item.get("confirmed_findings") or [])[:8]
        row["unverified_candidates"] = (item.get("unverified_candidates") or [])[:8]
        rows.append(row)
        if len(rows) >= limit:
            break
    out["results"] = rows
    return out


def _compact_requirement_vision(block: object, limit: int = 20) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {}
    out = {key: block.get(key) for key in ("counts", "not_run", "diagnostics") if key in block}
    rows = []
    for item in block.get("results") or []:
        if not isinstance(item, dict):
            continue
        rows.append({
            key: item.get(key)
            for key in (
                "status", "execution_state", "sentence", "rooms", "verdict",
                "reason", "pages_checked", "vision_calls", "vision_unclear",
                "zoom_calls", "document", "page", "comparability", "errors",
            )
            if key in item
        })
        if len(rows) >= limit:
            break
    if rows:
        out["results"] = rows
    return out


def collect_runtime_summary() -> dict[str, Any]:
    root = base._select_runtime_root()
    summary: dict[str, Any] = {
        "selected_root": str(root) if root else None,
        "stages": {},
    }
    if root is None:
        summary["warning"] = "No runtime logs found"
        return summary

    for run_type in ("pd", "compliance"):
        raw = base._latest_top_level(root, run_type)
        if raw:
            summary["stages"][run_type] = {
                "run_id": raw.get("run_id"),
                "status": raw.get("status"),
                "technical_status": raw.get("technical_status"),
                "execution_state": raw.get("execution_state"),
                "metrics": _metrics(raw),
                "errors": (raw.get("errors") or [])[:20],
            }

    for run_type in ("analysis", "triangulated"):
        raw = base._safe_json(root / "tasks" / run_type / "latest.json")
        if not raw:
            continue
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        stage = {
            "run_id": raw.get("run_id"),
            "status": raw.get("status"),
            "technical_status": raw.get("technical_status"),
            "execution_state": raw.get("execution_state"),
            "provider": raw.get("provider"),
            "model": raw.get("model"),
            "metrics": _metrics(raw),
            "errors": (raw.get("errors") or [])[:20],
        }
        for key in (
            "valid", "reason", "active_architecture", "legacy_runtime", "llm",
            "not_run", "performance", "pairs_total", "pairs_done",
            "pairs_llm_ok", "pairs_llm_error", "findings_total", "findings",
            "semantic_findings",
        ):
            if key in details:
                stage[key] = details.get(key)
        if "pair_vision" in details:
            stage["pair_vision"] = _compact_pair_vision(details.get("pair_vision"))
        if "vision_requirements" in details:
            stage["vision_requirements"] = _compact_requirement_vision(details.get("vision_requirements"))
        requirements = details.get("requirements")
        if isinstance(requirements, dict):
            stage["requirements"] = {
                "source": requirements.get("source"),
                "total": requirements.get("total"),
                "compliance": _compact_requirement_vision(requirements.get("compliance")),
            }
        summary["stages"][run_type] = stage

    ids = {
        name: stage.get("run_id")
        for name, stage in summary["stages"].items()
        if stage.get("run_id") is not None
    }
    summary["stage_run_ids"] = ids
    summary["run_ids_all_equal"] = len(set(ids.values())) <= 1 if ids else None

    tri = summary["stages"].get("triangulated") or {}
    metrics = tri.get("metrics") if isinstance(tri, dict) else {}
    requests = float((metrics or {}).get("requests") or 0)
    cache_hits = float((metrics or {}).get("persistent_cache_hits") or 0) + float((metrics or {}).get("result_cache_hits") or 0)
    if requests == 0 and cache_hits > 0:
        summary["freshness_hint"] = "cache_only"
    elif requests > 0 and cache_hits > 0:
        summary["freshness_hint"] = "mixed"
    elif requests > 0:
        summary["freshness_hint"] = "fresh"
    else:
        summary["freshness_hint"] = "unknown"
    return summary


def main() -> int:
    base.RUNTIME_SYSTEM = RUNTIME_SYSTEM
    base.ARCHITECTURE_SYSTEM = ARCHITECTURE_SYSTEM
    base.RUNTIME_PROMPT = RUNTIME_PROMPT
    base.ARCHITECTURE_PROMPT = ARCHITECTURE_PROMPT + "\n<CURRENT_ARCHITECTURE>\n" + CURRENT_ARCHITECTURE + "\n</CURRENT_ARCHITECTURE>"
    base._metrics = _metrics
    base.collect_runtime_summary = collect_runtime_summary
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
