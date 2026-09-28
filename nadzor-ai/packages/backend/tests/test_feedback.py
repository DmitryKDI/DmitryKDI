"""Обратная связь и управляемое дообучение (ТЗ 7, модули 4 и 10; 9.4; 14)."""
import datetime as dt
import uuid

from app import feedback, jobs, models
from app.db import SessionLocal
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
BBOX = [0.1, 0.1, 0.2, 0.2]


def _run(*, finalized: bool = False, object_id: str | None = None) -> tuple[int, str]:
    object_id = object_id or f"fb-{uuid.uuid4().hex[:8]}"
    checks = [{
        "finding_id": f"M-00{n}:matrix", "parameter_code": f"M-00{n}",
        "parameter_name": f"Параметр {n}", "finding_status": "CANDIDATE",
        "technical_status": "completed", "completeness_status": "COMPLETE",
        "explanation": "машинное объяснение",
        "evidence": [{"stage": "PD", "document_id": 1, "page": 1, "bbox": BBOX},
                     {"stage": "RD", "document_id": 2, "page": 3, "bbox": BBOX}],
    } for n in (1, 2, 3)]
    with SessionLocal() as db:
        run = models.OfficialRun(
            object_id=object_id, status="completed", input_snapshot=[],
            result={"matrix_version": "1.1", "checks": checks,
                    "coverage": {"total": 3, "completed": 3, "not_run": 0},
                    "graphic_analysis": {"status": "not_run", "candidates": []}},
            finalized_at=dt.datetime.utcnow() if finalized else None)
        db.add(run)
        db.commit()
        return run.id, object_id


def _decide(run_id: int, finding: str, status: str, reason_code: str = "") -> dict:
    version = client.get(f"/official/runs/{run_id}").json()["version"]
    response = client.post(f"/official/runs/{run_id}/decisions", json={
        "finding_id": finding, "status": status, "reason": "основание",
        "reason_code": reason_code, "expected_version": version})
    assert response.status_code == 200, response.text
    return response.json()


def test_decisions_become_labels_rejections_and_disputes():
    run_id, _ = _run()
    confirmed = _decide(run_id, "M-001:matrix", "CONFIRMED_VIOLATION")
    assert "положительный GOLD-кандидат" in confirmed["system_comment"]
    rejected = _decide(run_id, "M-002:matrix", "NEGATIVE_VERIFIED", "OCR_ERROR")
    assert rejected["system_comment"].startswith("Результат инспектора: NEGATIVE_VERIFIED. "
                                                 "Причина: OCR_ERROR.")
    disputed = _decide(run_id, "M-003:matrix", "CLARIFICATION_REQUIRED")
    assert "не включается в GOLD" in disputed["system_comment"]
    with SessionLocal() as db:
        items = {row.finding_id: row for row in db.query(models.DatasetItem).filter_by(
            run_id=run_id)}
        assert items["M-001:matrix"].label == feedback.POSITIVE
        assert items["M-002:matrix"].label == feedback.NEGATIVE
        assert "M-003:matrix" not in items
        assert all(row.status == "DRAFT" for row in items.values())
        assert items["M-001:matrix"].source_versions["input_manifest_hash"]
        rejection = db.query(models.RejectionLog).filter_by(run_id=run_id).one()
        assert rejection.rejection_reason == "OCR_ERROR" and rejection.suggested_fix
        assert rejection.ai_verdict.startswith("CANDIDATE")
        dispute = db.query(models.DisputeLog).filter_by(run_id=run_id).one()
        assert dispute.resolution_status == "OPEN"
    _decide(run_id, "M-003:matrix", "CONFIRMED_VIOLATION")
    with SessionLocal() as db:
        assert db.query(models.DisputeLog).filter_by(run_id=run_id).one().resolution_status \
            == "RESOLVED"
    _decide(run_id, "M-001:matrix", "CLARIFICATION_REQUIRED")
    with SessionLocal() as db:
        assert db.query(models.DatasetItem).filter_by(
            run_id=run_id, finding_id="M-001:matrix").one().status == "SUPERSEDED"
    print("OK: решения размечены; отклонения и спорные случаи — в своих журналах")


def test_release_takes_only_curated_items_of_finalized_protocols(act_as):
    open_run, _ = _run()
    final_run, object_id = _run(finalized=False)
    _decide(open_run, "M-001:matrix", "CONFIRMED_VIOLATION")
    _decide(final_run, "M-001:matrix", "CONFIRMED_VIOLATION")
    _decide(final_run, "M-002:matrix", "NEGATIVE_VERIFIED", "NO_DIFFERENCE")
    with SessionLocal() as db:
        db.get(models.OfficialRun, final_run).finalized_at = dt.datetime.utcnow()
        db.commit()
    drafts = {(row["run_id"], row["finding_id"]): row["id"]
              for row in client.get("/api/v1/ml/dataset/items?status=DRAFT").json()}
    act_as("inspector")
    assert client.post(f"/api/v1/ml/dataset/items/{drafts[(final_run, 'M-001:matrix')]}"
                       "/curate", json={"approve": True}).status_code == 403
    act_as("ml_engineer")
    for key, item_id in drafts.items():
        if key[0] in (open_run, final_run):
            client.post(f"/api/v1/ml/dataset/items/{item_id}/curate", json={"approve": True})
    released = client.post("/api/v1/ml/dataset/versions")
    assert released.status_code == 200, released.text
    version = released.json()
    with SessionLocal() as db:
        row = db.query(models.DatasetVersion).filter_by(version=version["version"]).one()
        released = [db.get(models.DatasetItem, item) for item in row.item_ids]
    runs = {item.run_id for item in released}
    assert final_run in runs and open_run not in runs, "запись нефинализированного протокола"
    assert set(version["split_hashes"]) == {"TRAIN", "VALIDATION", "HIDDEN_TEST"}
    assert sorted(item.label for item in released if item.run_id == final_run) == [
        feedback.NEGATIVE, feedback.POSITIVE]
    assert object_id in {item.object_id for item in released}
    print("OK: выпуск — только одобренное куратором из финализированных протоколов")


def test_object_keeps_its_split_forever():
    with SessionLocal() as db:
        first = feedback.split_of(db, "same-object")
        db.commit()
    with SessionLocal() as db:
        assert feedback.split_of(db, "same-object") == first
    print("OK: объект навсегда в одном наборе — train и test не пересекаются")


def test_model_is_published_only_after_acceptance_and_can_be_rolled_back(act_as):
    with SessionLocal() as db:
        dataset = models.DatasetVersion(version=f"ds-test-{uuid.uuid4().hex[:6]}",
                                        item_ids=[], split_hashes={"HIDDEN_TEST": "x"})
        db.add(dataset)
        db.commit()
        dataset_version = dataset.version
    good = {"dataset_version": dataset_version, "precision": 0.93, "recall": 0.85,
            "f1": 0.89, "false_positive_rate": 0.05,
            "per_category_metrics": {"АР": {"recall": 0.85}}}
    tag = uuid.uuid4().hex[:6]
    act_as("ml_engineer")
    first = client.post("/api/v1/ml/models", json={**good, "model_version": f"m-a-{tag}"}).json()
    assert first["acceptance"]["passed"], first
    weak = client.post("/api/v1/ml/models", json={
        **good, "model_version": f"m-weak-{tag}", "recall": 0.7}).json()
    assert not weak["acceptance"]["passed"]
    assert client.post(f"/api/v1/ml/models/{first['id']}/approve").status_code == 403
    act_as("admin")
    assert client.post(f"/api/v1/ml/models/{weak['id']}/approve").status_code == 409
    assert client.post(f"/api/v1/ml/models/{first['id']}/approve").status_code == 200

    act_as("ml_engineer")
    drop = client.post("/api/v1/ml/models", json={
        **good, "model_version": f"m-b-{tag}", "per_category_metrics": {"АР": {"recall": 0.82}},
        "false_positive_rate": 0.08}).json()
    assert not drop["acceptance"]["passed"], "Recall категории упал на 3 п.п."
    assert drop["previous_model"] == f"m-a-{tag}"
    better = client.post("/api/v1/ml/models", json={**good, "model_version": f"m-c-{tag}"}).json()
    act_as("admin")
    client.post(f"/api/v1/ml/models/{better['id']}/approve")
    assert client.get("/api/v1/ml/models").json()["published"] == f"m-c-{tag}"
    rolled = client.post(f"/api/v1/ml/models/{better['id']}/rollback").json()
    assert rolled["published"] == f"m-a-{tag}"
    print("OK: публикация — только после приёмки и подписи; откат возвращает прежнюю")


def test_weekly_report_is_generated_automatically():
    run_id, _ = _run()
    _decide(run_id, "M-002:matrix", "NEGATIVE_VERIFIED", "WRONG_REVISION")
    report = client.get("/api/v1/ml/report?days=7").json()
    assert report["rejections_by_reason"].get("WRONG_REVISION", 0) >= 1
    assert any("WRONG_REVISION" in line for line in report["recommendations"])
    with SessionLocal() as db:
        latest = max([row.period_end for row in db.query(models.WeeklyReport)]
                     + [dt.datetime.utcnow()])
    future = latest + dt.timedelta(days=8)
    assert jobs.weekly_report_due(future)
    assert not jobs.weekly_report_due(future + dt.timedelta(days=1))
    print("OK: еженедельный отчёт со статистикой отклонений и рекомендациями")


def test_release_exports_rows_in_gold_schema_fields(act_as):
    run_id, object_id = _run()
    _decide(run_id, "M-001:matrix", "CONFIRMED_VIOLATION")
    with SessionLocal() as db:
        db.get(models.OfficialRun, run_id).finalized_at = dt.datetime.utcnow()
        db.commit()
    item = next(row for row in client.get("/api/v1/ml/dataset/items?status=DRAFT").json()
                if row["run_id"] == run_id)
    act_as("ml_engineer")
    client.post(f"/api/v1/ml/dataset/items/{item['id']}/curate", json={"approve": True})
    version = client.post("/api/v1/ml/dataset/versions").json()["version"]
    rows = client.get(f"/api/v1/ml/dataset/versions/{version}/export").json()
    row = next(r for r in rows if r["object_id"] == object_id)
    for field in ("evidence_group_id", "finding_id", "matrix_code", "expected_value",
                  "actual_value", "source_expected_file_id", "source_expected_page",
                  "source_expected_bbox_polygon", "source_actual_file_id",
                  "source_actual_stage", "approved_change_ref", "completeness_status",
                  "finding_status", "expert_id", "timestamp", "expert_reason_code",
                  "dataset_version", "matrix_version", "model_version", "split"):
        assert field in row, field
    assert row["finding_status"] == "CONFIRMED_VIOLATION" and row["expert_id"] is not None
    assert row["split"] in {"TRAIN", "VALIDATION", "HIDDEN_TEST"}
    assert row["source_expected_stage"] == "PD" and row["source_actual_stage"] == "RD"
    assert row["evidence_group_id"].startswith("EG-")
    csv_text = client.get(f"/api/v1/ml/dataset/versions/{version}/export?format=csv").text
    assert csv_text.lstrip("\ufeff").startswith("evidence_group_id,finding_id,object_id")
    print("OK: выпуск набора выгружается в полях листа «Схема GOLD» (JSON и CSV)")
