"""Generate a small second-stage synthetic curriculum for ventilation reasoning.

This corpus is benchmark-safe: all room numbers, system marks, equipment names
and expected answers are fictional and independent from the real mini benchmark.
It is intended to continue training from already persisted generic memory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_synthetic_corpus import LESSONS, ROOT, _pdf

DEFAULT_FOCUS_OUT = ROOT / "data" / "synthetic_training" / "focus_v2"

FOCUS_CASES = json.loads((ROOT / "data" / "synthetic_curricula" / "focus_v2.json").read_text(encoding="utf-8"))


def generate_focus(out: Path = DEFAULT_FOCUS_OUT) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": "2.0-focus",
        "benchmark_safe": True,
        "purpose": "configuration_connection_local_exhaust_hard_negatives",
        "cases": [],
    }
    for case in FOCUS_CASES:
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

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate focused second-stage synthetic curriculum")
    parser.add_argument("--out", type=Path, default=DEFAULT_FOCUS_OUT)
    args = parser.parse_args()
    manifest = generate_focus(args.out)
    positives = sum(bool(row["positive"]) for row in manifest["cases"])
    negatives = len(manifest["cases"]) - positives
    print(f"Generated {len(manifest['cases'])} focus cases ({positives} positive, {negatives} hard negative) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
