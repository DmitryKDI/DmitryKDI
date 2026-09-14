"""Strict deep transfer comparator for real PD/RD checks.

This layer keeps the cheap whole-document pass but adds page-by-page requirement
extraction and evidence-gated requirement verification. It is benchmark-agnostic:
all requirements and retrieval terms come from the current PD/RD only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app.inspector_memory import lessons_prompt  # noqa: E402
from app.llm import call_llm_json  # noqa: E402
from requirement_driven_retrieval import (  # noqa: E402
    REQUIREMENT_COMPARE_SYSTEM,
    REQUIREMENT_EXTRACT_SYSTEM,
    normalize_requirements,
    requirement_batches,
    requirement_rd_context,
    requirements_prompt,
)
from simple_competition_compare import (  # noqa: E402
    VERIFY_SYSTEM,
    _config,
    _dedupe_suspicions,
    _pack_documents,
    detect_suspicions,
    extract_pdf_pages,
)

DEEP_BATCH_SIZE = int(os.environ.get("NADZOR_DEEP_BATCH_SIZE", "4"))
DEEP_PAGE_REQUIREMENT_LIMIT = int(os.environ.get("NADZOR_DEEP_PAGE_REQUIREMENT_LIMIT", "16"))

_UNSUPPORTED_MATCH_HINTS = (
    "подразумевает",
    "подразумевается",
    "вероятно",
    "предполож",
    "может быть",
    "по смыслу",
)


def _extract_requirements_by_page(pd_path: Path, config) -> list[dict]:
    """Extract independently from every PD page so one discipline cannot starve another."""
    requirements: list[dict] = []
    for page in extract_pdf_pages(pd_path):
        text = str(page.get("text") or "").strip()
        if len(text) < 20:
            continue
        payload = call_llm_json(
            config,
            REQUIREMENT_EXTRACT_SYSTEM,
            requirements_prompt([page]),
            operation="text_verify",
            source_digest=f"requirements-page:{pd_path}:p{page['page']}",
            prompt_version="requirements-page-v2",
            use_cache=False,
        )
        rows = normalize_requirements(payload, limit=DEEP_PAGE_REQUIREMENT_LIMIT)
        for row in rows:
            row["pd_page"] = int(page["page"])
            requirements.append(row)

    # Stable IDs independent of whatever numbering the LLM returned per page.
    for index, row in enumerate(requirements, 1):
        row["id"] = f"REQ-{index:03d}"
    return requirements


def _pd_context_for_batch(pd_pages: Sequence[dict], batch: Sequence[dict]) -> str:
    wanted = {int(x.get("pd_page") or 0) for x in batch if int(x.get("pd_page") or 0) > 0}
    rows = [x for x in pd_pages if int(x.get("page") or 0) in wanted] or list(pd_pages)
    parts = ["<PD_EVIDENCE_FOR_REQUIREMENTS>"]
    for row in rows:
        parts.append(f"[PD page {row.get('page')}]\n{row.get('text') or ''}")
    parts.append("</PD_EVIDENCE_FOR_REQUIREMENTS>")
    return "\n\n".join(parts)


def _valid_explicit_evidence(raw: dict) -> bool:
    evidence = raw.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            continue
        if str(item.get("quote") or "").strip() and str(item.get("document") or "").strip():
            return True
    return False


def _deep_requirement_pass(pd_path: Path, rd_paths: Sequence[Path], config):
    requirements = _extract_requirements_by_page(pd_path, config)
    pd_pages = extract_pdf_pages(pd_path)
    learned = lessons_prompt()
    suspicions: list[dict] = []
    coverage: list[dict] = []
    audit_rows: list[dict] = []

    for batch_index, batch in enumerate(requirement_batches(requirements, size=DEEP_BATCH_SIZE), 1):
        candidate_context, batch_coverage = requirement_rd_context(batch, rd_paths, extract_pdf_pages)
        coverage.extend(batch_coverage)
        prompt = (
            _pd_context_for_batch(pd_pages, batch)
            + "\n\n<REQUIREMENT_SET>\n"
            + json.dumps(batch, ensure_ascii=False)
            + "\n</REQUIREMENT_SET>\n\n"
            + candidate_context
            + learned
            + "\nПроверь каждое требование. matched допустим только при явной цитате РД в evidence."
        )
        payload = call_llm_json(
            config,
            REQUIREMENT_COMPARE_SYSTEM,
            prompt,
            operation="text_verify",
            source_digest=f"strict-requirement-compare:{pd_path}:batch{batch_index}:" + "|".join(map(str, rd_paths)),
            prompt_version="requirement-compare-v2-evidence",
            use_cache=False,
        )
        rows = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []

        batch_ids = {str(x.get("id") or "") for x in batch}
        seen_ids: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            req_id = str(raw.get("requirement_id") or "")
            if req_id not in batch_ids:
                continue
            seen_ids.add(req_id)
            status = str(raw.get("status") or "not_observed").strip().lower()
            reason = str(raw.get("reason") or "")

            if status == "matched":
                weak_reason = reason.casefold()
                if not _valid_explicit_evidence(raw) or any(hint in weak_reason for hint in _UNSUPPORTED_MATCH_HINTS):
                    status = "not_observed"
                    reason = "Matched downgraded: нет явного документального подтверждения требуемого решения в RD evidence. " + reason

            audit_rows.append({
                "requirement_id": req_id,
                "status": status,
                "reason": reason,
                "evidence": raw.get("evidence") if isinstance(raw.get("evidence"), list) else [],
            })

            suspicion = raw.get("suspicion")
            if status == "suspicion" and isinstance(suspicion, dict):
                row = dict(suspicion)
                row["source_requirement_id"] = req_id
                row["origin"] = "requirement_driven_v2"
                suspicions.append(row)

        for req_id in sorted(batch_ids - seen_ids):
            audit_rows.append({
                "requirement_id": req_id,
                "status": "not_observed",
                "reason": "requirement comparator returned no row",
                "evidence": [],
            })

    return requirements, coverage, audit_rows, suspicions


def _verify_all(pd_path: Path, rd_paths: Sequence[Path], config, suspicions: Sequence[dict]):
    documents = _pack_documents(pd_path, rd_paths)
    prompt = (
        documents
        + "\n<SUSPICIONS>\n"
        + json.dumps(list(suspicions), ensure_ascii=False)
        + "\n</SUSPICIONS>\nПерепроверь только эти подозрения. Точный ID нельзя подменять похожей сущностью."
    )
    payload = call_llm_json(
        config,
        VERIFY_SYSTEM,
        prompt,
        operation="text_verify",
        source_digest="strict-deep-verify:" + str(pd_path) + ":" + "|".join(map(str, rd_paths)),
        prompt_version="strict-deep-verify-v1",
        use_cache=False,
    )
    verification = payload.get("verification") if isinstance(payload, dict) else []
    if not isinstance(verification, list):
        verification = []
    by_id = {str(x.get("id") or ""): x for x in verification if isinstance(x, dict)}

    kept: list[dict] = []
    rejected: list[dict] = []
    for item in suspicions:
        row = dict(item)
        check = by_id.get(str(row.get("id") or ""), {
            "verdict": "needs_more",
            "reason": "verifier returned no row",
            "missing_evidence": [],
            "confidence": 0.0,
        })
        row["verification"] = check
        if str(check.get("verdict") or "").strip().lower() == "rejected":
            rejected.append(row)
        else:
            kept.append(row)
    return kept, rejected


def run(pd_path: Path, rd_paths: Sequence[Path], model: str) -> dict:
    config = _config(model)
    fast = detect_suspicions(pd_path, rd_paths, config, deep_retrieval=False)
    requirements, coverage, audit_rows, deep_suspicions = _deep_requirement_pass(pd_path, rd_paths, config)

    combined = _dedupe_suspicions(list(fast.get("suspicions") or []) + deep_suspicions)
    verified, rejected = _verify_all(pd_path, rd_paths, config, combined)
    return {
        "architecture": "hybrid_whole_document+per_page_requirement_retrieval_v2+strict_evidence_gate+verifier",
        "model": config.resolved_model(),
        "pd": str(pd_path),
        "rd": [str(x) for x in rd_paths],
        "priority_context_used": True,
        "deep_retrieval_used": True,
        "requirements_extracted": requirements,
        "requirement_coverage": coverage,
        "requirement_results": audit_rows,
        "suspicions": verified,
        "rejected_suspicions": list(fast.get("rejected_suspicions") or []) + rejected,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Strict real-transfer PD/RD comparator")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["NADZOR_BLIND_BENCHMARK"] = "0"
    result = run(args.pd, args.rd, args.model)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(f"Saved: {args.output}")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
