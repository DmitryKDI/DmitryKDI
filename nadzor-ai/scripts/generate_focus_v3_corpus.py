"""Generate benchmark-safe focus-v3 synthetic PD/RD cases.

The cases target generic ventilation reasoning: supply-unit configuration changes,
room-by-room exhaust coverage, topology changes, and hard negatives. All entity
ids and room numbers are fictional and unrelated to the real benchmark.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_synthetic_corpus import LESSONS, ROOT, _pdf

DEFAULT_FOCUS_V3_OUT = ROOT / "data" / "synthetic_training" / "focus_v3"

FOCUS_V3_CASES = json.loads((ROOT / "data" / "synthetic_curricula" / "focus_v3.json").read_text(encoding="utf-8"))


def generate_focus_v3(out: Path = DEFAULT_FOCUS_V3_OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "3.0-focus",
        "benchmark_safe": True,
        "purpose": "supply_unit_configuration_multiroom_exhaust_hard_negatives",
        "cases": [],
    }
    for case in FOCUS_V3_CASES:
        idx, category, title, room, mark, system, pd_text, rd_text, fact = case
        cid = f"SYN-{idx:03d}"
        cdir = out / cid
        cdir.mkdir(parents=True, exist_ok=True)
        pd_path = cdir / f"{cid}_PD.pdf"
        rd_path = cdir / f"{cid}_RD.pdf"
        _pdf(pd_path, case, rd=False)
        _pdf(rd_path, case, rd=True)
        positive = category != "negative_control"
        expected = None if not positive else {
            "type": category,
            "room": room,
            "mark": mark,
            "system": system,
            "pd_page": 1,
            "rd_page": 1,
            "fact": fact,
        }
        gt = {
            "case_id": cid,
            "category": category,
            "positive": positive,
            "expected": expected,
            "allowed_training_abstraction": {
                "category": category,
                "lesson": LESSONS[category],
            },
        }
        gt_path = cdir / "ground_truth.json"
        gt_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["cases"].append({
            "case_id": cid,
            "category": category,
            "positive": positive,
            "pd": str(pd_path.relative_to(out)),
            "rd": str(rd_path.relative_to(out)),
            "ground_truth": str(gt_path.relative_to(out)),
        })
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate focus-v3 synthetic curriculum")
    parser.add_argument("--out", type=Path, default=DEFAULT_FOCUS_V3_OUT)
    args = parser.parse_args()
    manifest = generate_focus_v3(args.out)
    positives = sum(bool(row["positive"]) for row in manifest["cases"])
    negatives = len(manifest["cases"]) - positives
    print(f"Generated {len(manifest['cases'])} focus-v3 cases ({positives} positive, {negatives} hard negative) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
