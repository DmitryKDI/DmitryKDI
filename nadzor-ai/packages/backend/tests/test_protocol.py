"""Протокол по ТЗ: сценарии, статусы процесса, таблицы, финализация.

Проверяется контракт, который принимают по ТЗ: словарь статусов и правила
жизненного цикла протокола. Сравнение документов здесь не участвует —
результат конвейера задаётся готовым.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, protocol  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


def _check(finding_id: str, status: str | None, completeness: str = "COMPLETE") -> dict:
    return {"finding_id": finding_id, "parameter_code": finding_id.split(":")[0],
            "finding_status": status, "completeness_status": completeness,
            "technical_status": "completed", "priority": "HIGH",
            "expected_value": "120", "actual_value": "125", "explanation": "x",
            "evidence": [{"document_id": 1, "file_id": "D1", "sha256": "a", "stage": "PD",
                          "role": "expected", "page": 1, "bbox": [0.1, 0.1, 0.2, 0.2],
                          "quote": "q"}]}


def _result(checks: list[dict]) -> dict:
    return {"matrix_version": "1.1", "checks": checks,
            "coverage": {"total": len(checks), "completed": len(checks), "not_run": 0},
            "graphic_analysis": {"status": "not_run", "candidates": []},
            "document_selection": {"selected": {"PD": [1], "RD": [2]}, "problems": {}}}


SNAPSHOT = [
    {"id": 1, "digest": "a", "metadata": {"stage": "PD", "document_code": "C-PD",
                                          "revision": "1", "approval_status": "APPROVED"}},
    {"id": 2, "digest": "b", "metadata": {"stage": "RD", "document_code": "C-RD",
                                          "revision": "1", "approval_status": "APPROVED"}},
]


def test_scenarios_follow_the_specification():
    def run(counts, problems=None):
        return protocol.scenario(protocol.upload_statuses(counts, problems or {}))
    assert run({"PD": 1, "RD": 1, "ID": 1}) == "FULL"
    assert run({"PD": 1, "RD": 2}) == "PD_RD_ONLY"
    assert run({"PD": 1, "ID": 1}) == "PD_ID_ONLY"
    assert run({"RD": 1, "ID": 1}) == "RD_ID_ONLY"
    assert run({"RD": 1}) == "SINGLE_ONLY"
    assert run({"PD": 1, "RD": 1}, {"RD": "две редакции"}) == "PARTIALLY_LOADED"
    statuses = protocol.upload_statuses({"PD": 1, "RD": 1}, {"RD": "x"})
    assert statuses == {"PD": "PD_UPLOADED", "RD": "RD_PARTIAL", "ID": "ID_MISSING"}


def test_process_status_follows_the_lifecycle():
    candidate = _result([_check("M-001:matrix", "CANDIDATE")])
    decided = _result([_check("M-001:matrix", "CONFIRMED_VIOLATION")])
    assert protocol.process_status("queued", False, None, 0) == "PENDING"
    assert protocol.process_status("running", False, None, 0) == "PARSING"
    assert protocol.process_status("completed", False, candidate, 0) == "READY"
    assert protocol.process_status("completed", False, candidate, 1) == "VERIFYING"
    assert protocol.process_status("completed", False, decided, 1) == "COMPLETED"
    assert protocol.process_status("completed", True, decided, 1) == "FINALIZED"
    assert protocol.process_status("error", False, None, 0) == "ERROR"
    assert not protocol.can_upload("FINALIZED") and not protocol.can_upload("PARSING")
    assert protocol.can_verify("READY") and not protocol.can_verify("COMPLETED")


def test_protocol_has_five_tables_cards_and_versions():
    checks = [_check("M-001:matrix", "CANDIDATE"), _check("M-002:matrix", "NEGATIVE_VERIFIED"),
              _check("M-003:matrix", None, "MISSING_EVIDENCE"),
              {**_check("graphic:1", "SUSPICION"), "parameter_code": "GRAPHIC"}]
    built = protocol.build(_result(checks), SNAPSHOT, run_status="completed",
                           finalized=False, decisions=0, model_version="m")
    tables = built["tables"]
    assert set(tables) == {"completeness", "candidates", "confirmed_violations",
                           "negative_verified", "suspicions"}
    assert [c["finding_id"] for c in tables["candidates"]] == ["M-001:matrix"]
    assert [c["finding_id"] for c in tables["suspicions"]] == ["graphic:1"]
    card = tables["candidates"][0]
    source = card["sources"][0]
    assert source["document_code"] == "C-PD" and source["revision"] == "1"
    assert source["approval_status"] == "APPROVED" and source["sha256"] == "a"
    assert card["approved_change_ref"] == "NONE"
    assert built["missing_evidence"] == ["M-003:matrix"]
    assert built["scenario"] == "PD_RD_ONLY"
    versions = built["versions"]
    assert versions["model_version"] == "m" and versions["matrix_version"] == "1.1"
    assert len(versions["input_manifest_hash"]) == 64


def test_delta_is_numeric_only():
    assert protocol.numeric_delta("120,5 м²", "125 м²") == 4.5
    assert protocol.numeric_delta("B30", "B25") == -5.0
    assert protocol.numeric_delta("бетон", "сталь") is None


def _stored_run(result: dict) -> int:
    db = next(get_session())
    try:
        run = models.OfficialRun(object_id="OBJ", status="completed", result=result,
                                 input_snapshot=SNAPSHOT, model_version="m")
        db.add(run)
        db.commit()
        return run.id
    finally:
        db.close()


def test_finalization_requires_decisions_and_locks_the_protocol():
    run_id = _stored_run(_result([_check("M-001:matrix", "CANDIDATE")]))
    body = client.get(f"/official/runs/{run_id}").json()
    assert body["process_status"] == "READY"

    blocked = client.post(f"/official/runs/{run_id}/finalize", json={"author": "insp"})
    assert blocked.status_code == 409 and "M-001:matrix" in blocked.json()["detail"]

    rejected = client.post(f"/official/runs/{run_id}/decisions", json={
        "finding_id": "M-001:matrix", "status": "NEGATIVE_VERIFIED", "author": "insp",
        "reason": "согласовано", "expected_version": 0})
    assert rejected.status_code == 422, "отклонение без кодированной причины"

    decided = client.post(f"/official/runs/{run_id}/decisions", json={
        "finding_id": "M-001:matrix", "status": "NEGATIVE_VERIFIED", "author": "insp",
        "reason": "согласовано", "reason_code": "APPROVED_CHANGE", "expected_version": 0})
    assert decided.status_code == 200, decided.text
    assert decided.json()["process_status"] == "COMPLETED"

    final = client.post(f"/official/runs/{run_id}/finalize", json={"author": "insp"}).json()
    assert final["process_status"] == "FINALIZED"
    assert final["verification_status"] == "PROTOCOL_FINALIZED"

    locked = client.post(f"/official/runs/{run_id}/decisions", json={
        "finding_id": "M-001:matrix", "status": "CONFIRMED_VIOLATION", "author": "insp",
        "reason": "x", "expected_version": 1})
    assert locked.status_code == 409


def test_unfinalize_needs_a_supervisor_and_is_logged():
    run_id = _stored_run(_result([_check("M-001:matrix", "NEGATIVE_VERIFIED")]))
    client.post(f"/official/runs/{run_id}/finalize", json={"author": "insp"})
    denied = client.post(f"/official/runs/{run_id}/unfinalize",
                         json={"author": "insp", "reason": "ошибка", "role": "inspector"})
    assert denied.status_code == 403
    reopened = client.post(f"/official/runs/{run_id}/unfinalize",
                           json={"author": "boss", "reason": "ошибка", "role": "supervisor"})
    assert reopened.json()["process_status"] == "COMPLETED"
    events = client.get(f"/official/runs/{run_id}/events").json()
    assert [e["action"] for e in events] == ["FINALIZE", "UNFINALIZE"]
    assert events[1]["reason"] == "ошибка"
