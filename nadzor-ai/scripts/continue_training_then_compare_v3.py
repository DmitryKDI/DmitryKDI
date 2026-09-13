"""Continue training on focus-v3 cases, then rerun real PD/RD transfer."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FOCUS_CORPUS = ROOT / "data" / "synthetic_training" / "focus_v3"
FOCUS_REPORT = ROOT / "data" / "synthetic_training" / "latest_focus_v3_training_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continue persistent-memory training on focus-v3 and recheck real PD/RD")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "synthetic_training" / "transfer_result_focus_v3.json")
    return parser.parse_args()


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT.parent)


def main() -> int:
    args = parse_args()
    _run([sys.executable, str(SCRIPTS / "generate_focus_v3_corpus.py")])
    _run([
        sys.executable,
        str(SCRIPTS / "train_synthetic_curriculum.py"),
        "--corpus", str(FOCUS_CORPUS),
        "--limit", str(max(1, args.limit)),
        "--retries", str(max(0, args.retries)),
        "--model", args.model,
        "--report", str(FOCUS_REPORT),
    ])
    command = [
        sys.executable,
        str(SCRIPTS / "simple_competition_compare.py"),
        "--pd", str(args.pd),
        "--model", args.model,
        "--output", str(args.output),
    ]
    for rd in args.rd:
        command.extend(["--rd", str(rd)])
    _run(command)
    print(f"\nFocus-v3 training report: {FOCUS_REPORT}")
    print(f"Transfer result: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
