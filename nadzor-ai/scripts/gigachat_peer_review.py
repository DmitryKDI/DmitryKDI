"""Two-stage blind peer review by the configured GigaChat model.

The script deliberately keeps benchmark ground truth out of the prompt.  It
reviews actual runtime diagnostics first, then the generic drawing-routing
architecture.  Two smaller JSON calls are used because a single oversized
strict-JSON review is less reliable with GigaChat.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))

from app.llm import LlmConfig, call_llm_json, credentials_from_file  # noqa: E402


RUNTIME_SYSTEM = """Ты — независимый technical peer reviewer runtime системы
НАДЗОР.ИИ. Анализируй только переданную execution-диагностику. Никаких
benchmark ground truth, expected rooms/sheets, known violations или догадок о
правильных findings. Technical failure нельзя трактовать как отсутствие
различий. Верни только компактный JSON без markdown."""

VISION_SYSTEM = """Ты — независимый technical peer reviewer vision-архитектуры
НАДЗОР.ИИ и эксперт по тому, как GigaChat-3-Ultra лучше анализировать
строительные чертежи ПД -> РД/ИД. Архитектура должна быть blind и не зависеть
от заранее известных помещений. Anchors — только navigation evidence, не
finding. Не используй benchmark ground truth и не делай legal/severity выводов.
Верни только компактный JSON без markdown."""

RUNTIME_PROMPT = """Проведи runtime review по ACTUAL_RUNTIME_SUMMARY ниже.

Особенно проверь:
1. отличаются ли technical_invalid / completed_with_errors / completed;
2. можно ли по данным утверждать, что Vision реально запускался;
3. как интерпретировать provider queue при concurrency=1 и progress 0/N;
4. достаточно ли RLock + atomic publication engine/session_factory для защиты
   lazy-init от split initialization race;
5. какие provider queue / wait / request / response метрики нужны;
6. как не смешивать latest разных стадий и нужен ли immutable run manifest;
7. pair score должен оставаться routing similarity, а не "процентом расхождения".

Верни:
{
  "runtime_assessment": {"proved": [], "not_proved": []},
  "load_race": {"assessment": "low|medium|high", "remaining_risks": []},
  "provider_scheduling": {"policy": "...", "progress_states": []},
  "pair_score_semantics": {"meaning": "...", "ui_label": "..."},
  "fail_closed": {"states": [], "no_difference_allowed_only_when": "..."},
  "run_correlation": {"recommendation": "...", "manifest_fields": []},
  "observability": [],
  "priority_changes": []
}
"""

VISION_PROMPT = """Проведи review universal high-recall routing.

Текущее направление:
- ROOM — один из сильных anchors, но не обязательный;
- fallback routing: room -> axis/system/equipment -> geometry -> PASS0 ->
  whole-page semantic compare;
- ROOM и generic region PASS1 получают 4 images: PD general/local + RD
  general/local;
- PASS1 перечисляет engineering entities/connections и только затем candidates;
- PASS2 confirmed/rejected/unclear;
- rejected не должен удалять candidate при слабой comparability;
- raster и pair score только routing/prioritization signals;
- generic geometry fingerprint используется лишь для proposal/pre-filter;
- PASS0 разрешено предлагать regions, но запрещено создавать findings.

Ответь с точки зрения модели, которая реально будет смотреть чертежи:
1. какой hybrid anchor order лучше для чертежей без помещений;
2. как локализовать axis cell/range, а не только подпись оси;
3. какой context нужен equipment/system anchor;
4. какие topology invariants добавить к geometry descriptor;
5. должен ли PASS0 идти до или после deterministic geometry proposals;
6. оптимальны ли 4 изображения и 2400-2600px long side;
7. какой universal comparison_region contract нужен без room_id;
8. какой comparability gate нужен PASS1/PASS2;
9. как сравнивать schematic <-> plan;
10. как делить discovery/verification budget между page pairs без starvation;
11. какие поля region diagnostics логировать;
12. что сейчас было бы overengineering.

Верни:
{
  "anchor_architecture": {},
  "axis_policy": {},
  "equipment_system_policy": {},
  "geometry_policy": {},
  "pass0_policy": {},
  "comparison_region": {},
  "image_policy": {},
  "comparability_gate": {},
  "schematic_vs_plan": {},
  "fair_scheduler": {},
  "observability": [],
  "avoid_overengineering": [],
  "next_changes": []
}
"""


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _credentials() -> str:
    _load_dotenv(ROOT / ".env")
    direct = os.environ.get("GIGACHAT_CREDENTIALS", "").strip()
    if direct:
        return direct
    client_id = os.environ.get("GIGACHAT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GIGACHAT_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return base64.b64encode(
            f"{client_id}:{client_secret}".encode("utf-8")
        ).decode("ascii")
    return credentials_from_file("gigachat")


def _safe_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _runtime_root_score(root: Path) -> int:
    """Prefer the backend logger directory, not the peer-review output folder."""
    if not root.is_dir():
        return -1
    score = 0
    for path in root.glob("*.json"):
        run_type = str(_safe_json(path).get("run_type") or "")
        if run_type in {"pd", "compliance"}:
            score += 3
    for run_type in ("analysis", "triangulated"):
        latest = root / "tasks" / run_type / "latest.json"
        if latest.is_file() and _safe_json(latest).get("run_type") == run_type:
            score += 8
    return score


def _runtime_root_candidates() -> list[Path]:
    """Каталоги, где могут лежать логи прогонов, в порядке доверия.

    Первым идёт тот, куда пишет ``app.run_logger``: если его переопределили
    переменной окружения, читать надо оттуда же, иначе сводка снова окажется
    пустой при существующих прогонах. ``packages/data/run_logs`` — устаревший
    каталог прежних версий, он оставлен только на чтение.
    """
    override = os.environ.get("NADZOR_RUN_LOGS_DIR")
    candidates = [Path(override)] if override else [ROOT / "data" / "run_logs"]
    candidates.append(ROOT / "packages" / "data" / "run_logs")
    candidates.append(ROOT / "run_logs")
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _select_runtime_root() -> Path | None:
    candidates = _runtime_root_candidates()
    ranked = sorted(
        ((_runtime_root_score(path), idx, path) for idx, path in enumerate(candidates)),
        key=lambda row: (-row[0], row[1]),
    )
    return ranked[0][2] if ranked and ranked[0][0] > 0 else None


def _metrics(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    keys = (
        "requests", "responses", "errors", "invalid_results", "retries",
        "rate_limits", "image_uploads", "image_cache_hits",
        "provider_queue_events", "provider_queue_wait_seconds",
        "mean_provider_queue_wait_seconds", "elapsed_seconds",
    )
    return {key: raw.get(key) for key in keys if key in raw}


def _latest_top_level(root: Path, run_type: str) -> dict[str, Any]:
    choices = []
    for path in root.glob("*.json"):
        data = _safe_json(path)
        if data.get("run_type") == run_type:
            choices.append((path.stat().st_mtime, data))
    return max(choices, default=(0.0, {}), key=lambda row: row[0])[1]


def _compact_task(data: dict[str, Any]) -> dict[str, Any]:
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    compact = {
        "run_id": data.get("run_id"),
        "run_type": data.get("run_type"),
        "execution_id": data.get("execution_id"),
        "status": data.get("status"),
        "technical_status": data.get("technical_status"),
        "execution_state": data.get("execution_state"),
        "metrics": _metrics(data),
        "errors_count": len(data.get("errors") or []),
    }
    allow = {
        "valid", "reason", "metrics_scope", "pairs_total", "pairs_done",
        "pairs_llm_ok", "pairs_llm_error", "findings_total",
        "pair_score_semantics", "not_run", "performance",
    }
    compact["details"] = {key: details.get(key) for key in allow if key in details}
    pairs = []
    for item in details.get("pairs") or []:
        if not isinstance(item, dict):
            continue
        pairs.append({
            key: item.get(key)
            for key in (
                "before_page", "after_page", "matched_by", "page_kind",
                "score", "discipline_mismatch", "llm_status",
            )
            if key in item
        })
    if pairs:
        compact["details"]["pairs"] = pairs[:40]
    return compact


def collect_runtime_summary() -> dict[str, Any]:
    root = _select_runtime_root()
    summary: dict[str, Any] = {
        "roots_checked": [str(path) for path in _runtime_root_candidates()],
        "selected_root": str(root) if root else None,
        "stages": {},
    }
    if root is None:
        summary["warning"] = "No actual runtime stage logs found"
        return summary

    for run_type in ("pd", "compliance"):
        data = _latest_top_level(root, run_type)
        if data:
            summary["stages"][run_type] = {
                "run_id": data.get("run_id"),
                "run_type": run_type,
                "status": data.get("status"),
                "technical_status": data.get("technical_status"),
                "execution_state": data.get("execution_state"),
                "metrics": _metrics(data),
                "errors_count": len(data.get("errors") or []),
            }
            block_name = "pd_stage" if run_type == "pd" else "compliance"
            block = data.get(block_name) if isinstance(data.get(block_name), dict) else {}
            summary["stages"][run_type][block_name] = {
                key: block.get(key)
                for key in ("requirements_total", "failed_chunks", "counts", "extractor")
                if key in block
            }

    for run_type in ("analysis", "triangulated"):
        data = _safe_json(root / "tasks" / run_type / "latest.json")
        if data:
            summary["stages"][run_type] = _compact_task(data)

    ids = {
        name: stage.get("run_id")
        for name, stage in summary["stages"].items()
        if stage.get("run_id") is not None
    }
    summary["stage_run_ids"] = ids
    summary["run_ids_all_equal"] = len(set(ids.values())) <= 1 if ids else None
    if len(set(ids.values())) > 1:
        summary["warning"] = (
            "latest stage files have different run_id values; do not treat them "
            "as one end-to-end execution without explicit correlation"
        )
    return summary


def _call_with_retry(
    config: LlmConfig,
    system_prompt: str,
    user_prompt: str,
    version: str,
    attempts: int = 3,
) -> dict[str, Any]:
    digest = hashlib.sha256(
        (system_prompt + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = call_llm_json(
                config,
                system_prompt,
                user_prompt,
                operation="text_verify",
                source_digest=digest,
                prompt_version=version,
                use_cache=False,
                timeout=180.0,
            )
            if not isinstance(result, dict) or not result:
                raise ValueError("GigaChat returned empty/non-object JSON")
            return result
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            delay = 2.0 * attempt
            print(
                f"{version}: attempt {attempt} failed: {type(exc).__name__}: "
                f"{exc}. Retry in {delay:.0f}s...",
                file=sys.stderr,
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def main() -> int:
    credentials = _credentials()
    if not credentials:
        print("GigaChat credentials not found.", file=sys.stderr)
        return 2

    config = LlmConfig(provider="gigachat", api_key=credentials)
    runtime_summary = collect_runtime_summary()
    runtime_user = (
        RUNTIME_PROMPT
        + "\n<ACTUAL_RUNTIME_SUMMARY>\n"
        + json.dumps(runtime_summary, ensure_ascii=False, indent=2)
        + "\n</ACTUAL_RUNTIME_SUMMARY>"
    )

    try:
        print("Stage 1/2: runtime peer review...", file=sys.stderr)
        runtime_review = _call_with_retry(
            config, RUNTIME_SYSTEM, runtime_user, "gigachat-peer-runtime-v7"
        )
        print("Stage 2/2: universal drawing peer review...", file=sys.stderr)
        vision_review = _call_with_retry(
            config, VISION_SYSTEM, VISION_PROMPT, "gigachat-peer-vision-v7"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"GigaChat peer review failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": config.resolved_model(),
        "blind": True,
        "review_topic": "runtime_and_universal_non_room_vision",
        "runtime_summary": runtime_summary,
        "runtime_review": runtime_review,
        "vision_review": vision_review,
    }
    out_dir = ROOT / "run_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "gigachat_peer_review_latest.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
