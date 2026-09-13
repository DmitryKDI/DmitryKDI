"""Run synthetic PD/RD curriculum and store only generalized lessons.

This is not weight fine-tuning. It evaluates GigaChat on synthetic document pairs,
then feeds only benchmark-safe generalized lessons into inspector memory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app.inspector_memory import learn_from_feedback  # noqa: E402
from app.llm import LlmConfig, credentials_from_file  # noqa: E402
from generate_synthetic_corpus import DEFAULT_OUT, generate  # noqa: E402
from simple_competition_compare import detect_suspicions  # noqa: E402


def _config(model: str) -> LlmConfig:
    credentials = os.environ.get("GIGACHAT_CREDENTIALS", "").strip() or credentials_from_file("gigachat")
    if not credentials:
        raise RuntimeError("GigaChat credentials not found in environment or local secrets/")
    return LlmConfig(provider="gigachat", api_key=credentials, model=model)


def _norm(value) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").split())


def _candidate_text(row: dict) -> str:
    return _norm(json.dumps(row, ensure_ascii=False))


def _is_hit(result: dict, expected: dict | None, positive: bool) -> tuple[bool, str]:
    suspicions = result.get("suspicions") if isinstance(result, dict) else []
    suspicions = [x for x in suspicions if isinstance(x, dict)] if isinstance(suspicions, list) else []
    if not positive:
        material = [x for x in suspicions if float(x.get("confidence") or 0.0) >= 0.55]
        return (not material, "negative_control_false_positive" if material else "negative_control_ok")
    if not expected:
        return False, "missing_ground_truth"
    room = _norm(expected.get("room")); system = _norm(expected.get("system")); kind = _norm(expected.get("type"))
    for row in suspicions:
        text = _candidate_text(row)
        room_ok = not room or room in text
        system_terms = [x for x in system.replace("/", " ").split() if x]
        system_ok = not system_terms or any(x in text for x in system_terms)
        kind_ok = not kind or kind in _norm(row.get("difference_type")) or kind in text
        if room_ok and system_ok and kind_ok:
            return True, "matched_room_system_type"
        if room_ok and system_ok:
            return True, "matched_room_system"
    return False, "no_matching_suspicion"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate synthetic corpus and teach generalized lessons")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--no-teach", action="store_true")
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "synthetic_training" / "latest_training_report.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["NADZOR_BLIND_BENCHMARK"] = "0"
    if not (args.corpus / "manifest.json").exists():
        generate(args.corpus)
    manifest = json.loads((args.corpus / "manifest.json").read_text(encoding="utf-8"))
    config = _config(args.model)
    rows = []
    lessons_to_teach: list[str] = []
    selected = manifest.get("cases", [])[: max(1, args.limit)]
    for index, item in enumerate(selected, 1):
        cid = item["case_id"]
        gt = json.loads((args.corpus / item["ground_truth"]).read_text(encoding="utf-8"))
        pd_path = args.corpus / item["pd"]
        rd_path = args.corpus / item["rd"]
        print(f"[{index}/{len(selected)}] {cid} {gt['category']}")
        try:
            result = detect_suspicions(pd_path, [rd_path], config)
            hit, reason = _is_hit(result, gt.get("expected"), bool(gt.get("positive")))
            error = ""
        except Exception as exc:
            result = {"suspicions": []}
            hit, reason = False, "runtime_error"
            error = f"{type(exc).__name__}: {exc}"
        if not hit:
            lesson = str(((gt.get("allowed_training_abstraction") or {}).get("lesson") or "")).strip()
            if lesson and lesson not in lessons_to_teach:
                lessons_to_teach.append(lesson)
        rows.append({
            "case_id": cid,
            "category": gt.get("category"),
            "positive": bool(gt.get("positive")),
            "hit": hit,
            "reason": reason,
            "error": error,
            "suspicions": result.get("suspicions") if isinstance(result, dict) else [],
        })
    passed = sum(bool(r["hit"]) for r in rows)
    teacher_ids: list[int] = []
    if lessons_to_teach and not args.no_teach:
        feedback = (
            "SYNTHETIC CURRICULUM GENERALIZED FEEDBACK. "
            "Do not preserve any case-specific identifiers. Convert only these generic habits into reusable lessons:\n- "
            + "\n- ".join(lessons_to_teach)
        )
        teacher_ids = learn_from_feedback(
            config,
            feedback,
            source_type="synthetic_curriculum",
            source_id="synthetic-v1",
        )
    report = {
        "architecture": "synthetic_curriculum->simple_two_pass->generic_memory",
        "model": config.resolved_model(),
        "cases": len(rows),
        "passed": passed,
        "score": passed / len(rows) if rows else 0.0,
        "missed_categories": sorted({r["category"] for r in rows if not r["hit"]}),
        "generalized_lessons_requested": lessons_to_teach,
        "stored_lesson_ids": teacher_ids,
        "results": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Score: {passed}/{len(rows)} = {report['score']:.1%}")
    print(f"Stored generalized lessons: {teacher_ids}")
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
