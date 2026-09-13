from __future__ import annotations

import json

from app import run_logger


def test_technical_status_is_fail_closed():
    assert run_logger._technical_status("done", []) == "completed"
    assert run_logger._technical_status("done", [{"error": "x"}]) == "completed_with_errors"
    assert run_logger._technical_status("done", [], {"valid": False}) == "technical_invalid"
    assert run_logger._technical_status("running", []) == "running"


def test_task_snapshots_keep_only_latest(tmp_path, monkeypatch):
    run_root = tmp_path / "run_logs"
    task_root = run_root / "tasks"
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", run_root)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", task_root)

    first = run_logger.save_task_snapshot(
        run_type="analysis",
        run_id=12,
        status="done",
        metrics={"requests": 1},
        details={"pairs_total": 1},
    )
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    assert first_payload["run_id"] == 12

    second = run_logger.save_task_snapshot(
        run_type="analysis",
        run_id=13,
        status="done",
        metrics={"requests": 2},
        details={"pairs_total": 2},
    )

    latest = task_root / "analysis" / "latest.json"
    assert first == latest
    assert second == latest
    assert latest.exists()
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["run_id"] == 13
    assert payload["technical_status"] == "completed"
    assert payload["execution_id"] == "analysis:13"
    assert payload["metrics"]["requests"] == 2
    assert payload["details"]["pairs_total"] == 2
    assert list((task_root / "analysis").glob("*.json")) == [latest]


def test_compact_requirement_results_keep_bbox_and_local_evidence():
    block = {
        "counts": {"требует проверки": 1},
        "items": [{"large": "not needed"}],
        "diagnostics": {
            "architecture": "universal",
            "vision_calls": 3,
            "zoom_calls": 1,
            "results": [{
                "requirement_index": 1,
                "requirement": "Предусмотреть X",
                "pd_document": "pd.pdf",
                "pd_page": 2,
                "final_state": "NOT_OBSERVED_ON_THIS_EVIDENCE",
                "candidate_findings": [{"difference": "hypothesis"}],
                "confirmed_findings": [],
                "pages": [{
                    "document": "rd.pdf",
                    "page": 4,
                    "candidate_regions": [{"rd_bbox_norm": [0.1, 0.2, 0.3, 0.4]}],
                    "checked_regions": [{
                        "rd_bbox_norm": [0.1, 0.2, 0.3, 0.4],
                        "evidence_state": "NOT_OBSERVED_ON_THIS_EVIDENCE",
                    }],
                }],
                "additional_evidence_needed": ["спецификация"],
            }],
        },
    }

    compact = run_logger._compact_visual_results(block)
    assert "items" not in compact
    assert compact["diagnostics"]["zoom_calls"] == 1
    row = compact["results"][0]
    assert row["final_state"] == "NOT_OBSERVED_ON_THIS_EVIDENCE"
    assert row["pages"][0]["checked_regions"][0]["rd_bbox_norm"] == [0.1, 0.2, 0.3, 0.4]
    assert row["additional_evidence_needed"] == ["спецификация"]


def test_compact_pair_results_keep_scope_and_checked_regions():
    block = {
        "counts": {"unclear": 1},
        "results": [{
            "pair_key": "0:1->0:2",
            "status": "not_observed_on_this_evidence",
            "evidence_state": "NOT_OBSERVED_ON_THIS_EVIDENCE",
            "pd_sheet": {"sheet_type": "SCHEMATIC"},
            "rd_sheet": {"sheet_type": "PLAN"},
            "candidate_regions": [{"rd_bbox_norm": [0.2, 0.2, 0.5, 0.5]}],
            "checked_regions": [{
                "rd_bbox_norm": [0.2, 0.2, 0.5, 0.5],
                "evidence_state": "NOT_OBSERVED_ON_THIS_EVIDENCE",
            }],
            "additional_evidence_needed": ["detail"],
        }],
    }
    compact = run_logger._compact_visual_results(block)
    row = compact["results"][0]
    assert row["pd_sheet"]["sheet_type"] == "SCHEMATIC"
    assert row["rd_sheet"]["sheet_type"] == "PLAN"
    assert row["checked_regions"][0]["rd_bbox_norm"] == [0.2, 0.2, 0.5, 0.5]
