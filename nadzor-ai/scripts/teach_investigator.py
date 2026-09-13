"""Store generalized investigator lessons from teacher/evaluator feedback."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))

from app.inspector_memory import learn_from_feedback  # noqa: E402
from app.llm import LlmConfig, credentials_from_file  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teach generalized search lessons to the NADZOR investigator")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--feedback", help="teacher feedback text")
    group.add_argument("--feedback-file", type=Path, help="UTF-8 file with teacher/evaluator feedback")
    parser.add_argument("--source-type", default="teacher")
    parser.add_argument("--source-id", default="")
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    feedback = args.feedback or args.feedback_file.read_text(encoding="utf-8")
    credentials = os.environ.get("GIGACHAT_CREDENTIALS", "").strip() or credentials_from_file("gigachat")
    if not credentials:
        raise SystemExit("GigaChat credentials not found in environment or local secrets/")
    config = LlmConfig(provider="gigachat", api_key=credentials, model=args.model)
    ids = learn_from_feedback(
        config,
        feedback,
        source_type=args.source_type,
        source_id=args.source_id,
    )
    if not ids:
        print("No reusable generic lessons were extracted.")
        return 0
    print(f"Stored {len(ids)} generalized lesson(s): {', '.join(map(str, ids))}")
    print("Blind benchmark mode ignores this memory completely.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
