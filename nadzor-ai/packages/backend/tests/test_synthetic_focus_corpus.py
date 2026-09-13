from pathlib import Path
import json
import sys

import fitz

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_focus_corpus import FOCUS_CASES, generate_focus  # noqa: E402


def test_focus_case_mix():
    assert len(FOCUS_CASES) == 12
    positive = [row for row in FOCUS_CASES if row[1] != "negative_control"]
    negative = [row for row in FOCUS_CASES if row[1] == "negative_control"]
    assert len(positive) == 8
    assert len(negative) == 4
    categories = {row[1] for row in FOCUS_CASES}
    assert {"configuration", "connection", "single_room", "absence", "cross_sheet", "negative_control"} <= categories


def test_focus_generator_writes_entity_mark_to_ground_truth(tmp_path):
    manifest = generate_focus(tmp_path)
    assert len(manifest["cases"]) == 12
    first = manifest["cases"][0]
    pd_path = tmp_path / first["pd"]
    gt_path = tmp_path / first["ground_truth"]
    with fitz.open(pd_path) as doc:
        assert "AHU-X31" in doc[0].get_text("text")
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["expected"]["mark"] == "AHU-X31"
    assert gt["allowed_training_abstraction"]["lesson"]
