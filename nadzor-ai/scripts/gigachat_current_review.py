"""Blind GigaChat review for the active stateful investigator architecture."""
from __future__ import annotations

import json
from typing import Any

import gigachat_peer_review as base


RUNTIME_SYSTEM = """Ты — независимый technical peer reviewer НАДЗОР.ИИ.
Оцени фактический runtime по логам. Ground truth benchmark, ожидаемые листы,
помещения и known violations тебе не даны. Не угадывай их. Technical silence,
незавершённый investigator, исчерпанный turn budget или verifier error нельзя
трактовать как отсутствие расхождений. Верни только JSON."""


ARCHITECTURE_SYSTEM = """Ты — независимый reviewer архитектуры НАДЗОР.ИИ и
эксперт по GigaChat-3-Ultra. Текущая система специально приблизилась к
обычному чату: один stateful investigator помнит предыдущие ходы, а Python
даёт ему инструменты search/open-page/zoom. Candidate finding подтверждает
отдельный verifier. Дай model-specific рекомендации без benchmark hints.
Верни только JSON."""


CURRENT_ARCHITECTURE = """CURRENT IMPLEMENTATION:
1. PD text requirements извлекаются один раз и становятся частью общего
   контекста расследования, а не отдельным микропайплайном verification.
2. Один stateful investigator получает compact document map всех страних и
   историю собственных предыдущих ответов. Он сам выбирает search,
   inspect_pages, zoom, propose_finding или finish.
3. Python tools — только руки: поиск/рендер/zoom. Они ничего не доказывают и
   не могут отфильтровать страницу как «неважную».
4. Перед окончательным finish есть обязательный self-review/red-team против
   false negatives: multi-room requirements, wrong genre, unopened legends,
   adjacent sheets, unknown topology/configuration changes.
5. Investigator НЕ имеет права сам подтвердить finding. Каждый candidate
   проходит отдельный stateless verifier по фактическим PD/RD evidence.
6. NOT_OBSERVED никогда не равен ABSENCE. Presence/absence verifier требует
   scope_sufficient + absence_scope_complete.
7. Learned inspector lessons живут только локально в SQLite. В blind mode
   NADZOR_BLIND_BENCHMARK=1 они полностью отключены, поэтому teacher/evaluator
   feedback не загрязняет повторный blind benchmark.
8. Legacy room/equipment/routing/verdict runtimes остаются только для
   compatibility/tests и не являются active gate.
9. В выставочном/experienced режиме приоритет — recall и глубина анализа, а не
   минимизация токенов. Ограничение всё равно bounded max_turns.
"""


RUNTIME_PROMPT = """Разбери ACTUAL_RUNTIME_SUMMARY.
Особенно проверь:
1. fresh ли inference: requests/responses/errors/cache hits;
2. запускался ли именно stateful investigator;
3. finished/self_reviewed/turn_budget_exhausted;
4. сколько страниц реально opened и сколько zoom regions;
5. какие actions модель выбирала и не застряла ли в search/finish слишком рано;
6. сколько candidates предложено, сколько verifier confirmed/unresolved;
7. были ли verifier technical failures;
8. покрытие PD requirements: не завершился ли поиск после проверки только части
   multi-room requirement;
9. не выглядит ли document map слишком бедным, чтобы модель сама находила
   нужный лист;
10. что логировать/менять в следующем blind run.

Верни JSON:
{
 "freshness":{"classification":"fresh|cache_only|mixed|unknown","evidence":[]},
 "investigator_execution":{
   "ran":false,"finished":false,"self_reviewed":false,
   "turn_budget_exhausted":false,"pages_inspected":0,"zoom_regions":0,
   "action_pattern":[],"technical_gaps":[],"coverage_gaps":[]
 },
 "verifier":{"candidates":0,"confirmed":0,"unresolved":0,"technical_gaps":[]},
 "false_negative_risks":[],
 "false_positive_risks":[],
 "logging_changes":[],
 "priority_runtime_changes":[]
}
"""


ARCHITECTURE_PROMPT = """Проведи review CURRENT_ARCHITECTURE именно как
GigaChat-3-Ultra agent architecture.

Ответь:
1. Насколько stateful chat + tools лучше серии изолированных JSON micro-prompts
   для сложного чтения инженерных чертежей?
2. Что должно оставаться в длинной conversation history, а что лучше
   суммаризировать, чтобы не засорять контекст?
3. Какие действия/инструменты нужны investigator кроме search/open/zoom?
4. Как не дать модели завершиться слишком рано, но не заставлять бессмысленно
   просматривать всё подряд?
5. Как проводить self-review против false negatives?
6. Как verifier должен подтверждать configuration/topology и отдельно absence?
7. Как учиться на teacher feedback: что хранить как generic lessons, а что
   никогда не переносить между объектами?
8. Как разделить experienced/demo memory и честный blind benchmark?
9. Какие старые routing/pair/requirement micro-runtimes можно окончательно
   убрать из active path?
10. TOP-7 следующих улучшений без подгонки под известный benchmark.

Верни JSON:
{
 "stateful_context":{"keep":[],"summarize":[],"risks":[]},
 "toolset":{"keep":[],"add":[],"remove":[]},
 "stop_policy":{"rules":[],"self_review":[]},
 "verifier":{"configuration":[],"absence":[]},
 "learning_memory":{"store":[],"never_store":[],"blind_policy":[]},
 "simplify_or_remove":[],
 "top_7_changes":[]
}
"""


def _metrics(raw: dict[str, Any]) -> dict[str, Any]:
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    keys = (
        "requests", "responses", "errors", "invalid_results", "retries",
        "rate_limits", "image_uploads", "image_cache_hits", "result_cache_hits",
        "persistent_cache_hits", "text_characters", "text_batches",
        "elapsed_seconds", "mean_response_seconds",
    )
    return {key: metrics.get(key) for key in keys if key in metrics}


def collect_runtime_summary() -> dict[str, Any]:
    root = base._select_runtime_root()
    summary: dict[str, Any] = {"selected_root": str(root) if root else None, "stages": {}}
    if root is None:
        summary["warning"] = "No runtime logs found"
        return summary

    raw = base._safe_json(root / "tasks" / "triangulated" / "latest.json")
    if raw:
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        investigator = details.get("investigator") if isinstance(details.get("investigator"), dict) else {}
        pair_vision = details.get("pair_vision") if isinstance(details.get("pair_vision"), dict) else {}
        coverage = pair_vision.get("coverage") if isinstance(pair_vision.get("coverage"), dict) else {}
        # Older/compact run_logger snapshots may not carry the top-level
        # investigator block. The active runtime mirrors the key execution
        # metrics into pair_vision.coverage specifically so peer review still
        # sees stateful coverage instead of silently treating it as absent.
        if not investigator and coverage:
            investigator = {
                "architecture": coverage.get("semantic_architecture"),
                "max_turns": coverage.get("max_turns"),
                "turns_used": coverage.get("turns_used"),
                "finished": coverage.get("status") == "complete",
                "self_reviewed": coverage.get("self_reviewed"),
                "turn_budget_exhausted": coverage.get("turn_budget_exhausted"),
                "action_counts": coverage.get("action_counts") or {},
                "pages_total": coverage.get("pages_total"),
                "pages_inspected": coverage.get("pages_inspected"),
                "zoom_regions_total": coverage.get("zoom_regions_total"),
                "requirements_total": coverage.get("requirements_total"),
                "candidates_total": coverage.get("candidates_total"),
                "confirmed_total": coverage.get("confirmed_total"),
                "unresolved_total": coverage.get("unresolved_total"),
                "verifier_errors": coverage.get("verifier_errors"),
                "errors": coverage.get("errors") or [],
            }
        summary["stages"]["triangulated"] = {
            "run_id": raw.get("run_id"),
            "status": raw.get("status"),
            "technical_status": raw.get("technical_status"),
            "execution_state": raw.get("execution_state"),
            "provider": raw.get("provider"),
            "model": raw.get("model"),
            "metrics": _metrics(raw),
            "valid": details.get("valid"),
            "active_architecture": details.get("active_architecture"),
            "llm": details.get("llm"),
            "semantic_findings": (details.get("semantic_findings") or [])[:20],
            "semantic_candidates": (details.get("semantic_candidates") or [])[:20],
            "requirements_total": (
                details.get("requirements", {}).get("total")
                if isinstance(details.get("requirements"), dict) else None
            ),
            "investigator": {
                key: investigator.get(key)
                for key in (
                    "architecture", "model", "blind_mode", "learned_lessons_enabled",
                    "max_turns", "turns_used", "finished", "finish_requested",
                    "self_reviewed", "turn_budget_exhausted", "action_counts",
                    "pages_total", "pages_inspected", "inspected_refs",
                    "zoom_regions_total", "requirements_total", "candidates_total",
                    "confirmed_total", "unresolved_total", "verifier_errors",
                    "errors", "transcript",
                )
                if key in investigator
            },
        }

    tri = summary["stages"].get("triangulated") or {}
    metrics = tri.get("metrics") if isinstance(tri, dict) else {}
    requests = float((metrics or {}).get("requests") or 0)
    hits = float((metrics or {}).get("persistent_cache_hits") or 0) + float(
        (metrics or {}).get("result_cache_hits") or 0
    )
    summary["freshness_hint"] = (
        "cache_only" if requests == 0 and hits > 0
        else "mixed" if requests > 0 and hits > 0
        else "fresh" if requests > 0
        else "unknown"
    )
    return summary


def main() -> int:
    base.RUNTIME_SYSTEM = RUNTIME_SYSTEM
    base.ARCHITECTURE_SYSTEM = ARCHITECTURE_SYSTEM
    base.RUNTIME_PROMPT = RUNTIME_PROMPT
    base.ARCHITECTURE_PROMPT = (
        ARCHITECTURE_PROMPT
        + "\n<CURRENT_ARCHITECTURE>\n"
        + CURRENT_ARCHITECTURE
        + "\n</CURRENT_ARCHITECTURE>"
    )
    base._metrics = _metrics
    base.collect_runtime_summary = collect_runtime_summary
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
