"""Дашборд инспектора: цвет объекта и фильтры (ТЗ 7, модуль 7)."""
import datetime as dt
import uuid

from app import models
from app.db import SessionLocal
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
BBOX = [0.1, 0.1, 0.2, 0.2]


def _run(tag: str, finding_status: str | None, *, section: str = "АР") -> str:
    object_id = f"dash-{tag}-{uuid.uuid4().hex[:6]}"
    check = {"finding_id": "M-001:matrix", "parameter_code": "M-001", "section": section,
             "finding_status": finding_status, "technical_status": "completed",
             "completeness_status": "COMPLETE",
             "evidence": [{"stage": "PD", "document_id": 1, "page": 1, "bbox": BBOX},
                          {"stage": "RD", "document_id": 2, "page": 1, "bbox": BBOX}]}
    with SessionLocal() as db:
        db.add(models.OfficialRun(
            object_id=object_id, status="completed", input_snapshot=[],
            result={"matrix_version": "1.1", "checks": [check],
                    "coverage": {"total": 1, "completed": 1, "not_run": 0},
                    "graphic_analysis": {"status": "not_run", "candidates": []}}))
        db.commit()
    return object_id


def test_objects_are_colored_and_filtered():
    red = _run("red", "CANDIDATE", section="КР")
    yellow = _run("yellow", "CANDIDATE")
    green = _run("green", "NEGATIVE_VERIFIED")
    with SessionLocal() as db:
        run = db.query(models.OfficialRun).filter_by(object_id=red).one()
        db.add(models.InspectorDecision(run_id=run.id, version=1, finding_id="M-001:matrix",
                                        status="CONFIRMED_VIOLATION", author="t", reason="t"))
        db.commit()
    body = client.get("/api/v1/dashboard").json()
    colors = {item["object_id"]: item["color"] for item in body["objects"]}
    assert (colors[red], colors[yellow], colors[green]) == ("red", "yellow", "green")
    only_red = client.get("/api/v1/dashboard?color=red").json()["objects"]
    assert red in {item["object_id"] for item in only_red}
    assert yellow not in {item["object_id"] for item in only_red}
    by_section = {item["object_id"] for item in
                  client.get("/api/v1/dashboard?section=КР").json()["objects"]}
    assert red in by_section and yellow not in by_section
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert client.get(f"/api/v1/dashboard?date_from={tomorrow}").json()["objects"] == []
    print("OK: объекты окрашены по результатам и фильтруются по разделу, цвету и дате")
