from pathlib import Path
import json
import sys

import fitz

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_synthetic_corpus import CASES, generate  # noqa: E402
from simple_competition_compare import DETECT_SYSTEM, VERIFY_SYSTEM  # noqa: E402


def test_synthetic_case_mix_is_stable():
    assert len(CASES) == 30
    positive = [row for row in CASES if row[1] != "negative_control"]
    negative = [row for row in CASES if row[1] == "negative_control"]
    assert len(positive) == 27
    assert len(negative) == 3
    categories = {row[1] for row in CASES}
    assert {"deletion", "quantity", "parameter", "location", "connection", "configuration", "single_room", "cross_sheet", "negative_control"} <= categories


def test_generator_writes_readable_pdf_and_ground_truth(tmp_path):
    manifest = generate(tmp_path)
    assert len(manifest["cases"]) == 30
    first = manifest["cases"][0]
    pd_path = tmp_path / first["pd"]
    rd_path = tmp_path / first["rd"]
    gt_path = tmp_path / first["ground_truth"]
    assert pd_path.exists() and rd_path.exists() and gt_path.exists()
    with fitz.open(pd_path) as doc:
        assert len(doc) == 2
        assert "EX-501" in doc[0].get_text("text")
    with fitz.open(rd_path) as doc:
        assert len(doc) == 2
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["positive"] is True
    assert gt["allowed_training_abstraction"]["lesson"]


def test_simple_comparator_rejects_wording_only_differences_and_id_substitution():
    detector = DETECT_SYSTEM.casefold()
    verifier = VERIFY_SYSTEM.casefold()
    assert "отличие формулировки само по себе не является инженерным отклонением" in detector
    assert "semantic equivalence" in verifier
    assert "verdict=rejected" in verifier
    assert "направление описания фразы" in verifier
    assert "номер помещения/марка — идентификатор" in verifier
