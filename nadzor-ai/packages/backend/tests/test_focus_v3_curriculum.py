from pathlib import Path
import json
import sys

import fitz

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_focus_v3_corpus import FOCUS_V3_CASES, generate_focus_v3  # noqa: E402


def test_focus_v3_mix_is_balanced():
    assert len(FOCUS_V3_CASES) == 12
    positive = [row for row in FOCUS_V3_CASES if row[1] != "negative_control"]
    negative = [row for row in FOCUS_V3_CASES if row[1] == "negative_control"]
    assert len(positive) == 8
    assert len(negative) == 4
    categories = {row[1] for row in FOCUS_V3_CASES}
    assert {"configuration", "connection", "single_room", "absence", "cross_sheet", "negative_control"} <= categories


def test_focus_v3_generator_writes_benchmark_safe_cases(tmp_path):
    manifest = generate_focus_v3(tmp_path)
    assert manifest["benchmark_safe"] is True
    assert len(manifest["cases"]) == 12
    first = manifest["cases"][0]
    pd_path = tmp_path / first["pd"]
    rd_path = tmp_path / first["rd"]
    gt_path = tmp_path / first["ground_truth"]
    with fitz.open(pd_path) as doc:
        assert "AHU-Y43" in doc[0].get_text("text")
    with fitz.open(rd_path) as doc:
        assert len(doc) == 2
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["expected"]["mark"] == "AHU-Y43"
    assert gt["allowed_training_abstraction"]["lesson"]
