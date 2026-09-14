"""One-command synthetic curriculum followed by a real PD/RD transfer check."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic corpus, teach generic lessons, then compare real PD/RD")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "synthetic_training" / "transfer_result.json")
    return parser.parse_args()


def _run(args: list[str]) -> None:
    print("+", " ".join(args))
    subprocess.run(args, check=True, cwd=ROOT.parent)


def main() -> int:
    args = parse_args()
    _run([sys.executable, str(SCRIPTS / "generate_synthetic_corpus.py")])
    _run([
        sys.executable,
        str(SCRIPTS / "train_synthetic_curriculum.py"),
        "--limit", str(max(1, args.limit)),
        "--model", args.model,
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
    print(f"\nTransfer result: {args.output}")
    print("Synthetic training report: nadzor-ai/data/synthetic_training/latest_training_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
