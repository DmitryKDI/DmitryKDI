"""Версии официального протокола и решения инспектора не меняют машинный снимок."""

import datetime as dt

import pytest
from app import models, official_api
from app.db import Base
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def complete_result(*, evidence=True):
    sources = ([{"document_id": 1, "stage": "PD", "page": 1,
                 "bbox": [0.1, 0.1, 0.2, 0.2], "quote": "цитата"}]
               if evidence else [])
    return {
        "checks": [{
            "finding_id": "M-001:matrix", "parameter_code": "M-001",
            "parameter_name": "Параметр", "priority": "HIGH",
            "completeness_status": "COMPLETE", "finding_status": "CANDIDATE",
            "technical_status": "completed", "explanation": "Гипотеза",
            "expected_value": "1", "actual_value": "2", "evidence": sources,
            "review_history": [],
        }],
        "coverage": {"total": 1, "completed": 1, "not_run": 0},
    }


def test_decision_is_versioned_without_mutating_machine_result(db):
    run = models.OfficialRun(
        object_id="synthetic", status="completed", result=complete_result(),
        input_snapshot=[],
    )
    db.add(run)
    db.commit()
    raw_before = run.result

    view = official_api.decide(run.id, official_api.DecisionInput(
        finding_id="M-001:matrix", status="CONFIRMED_VIOLATION",
        author="Инспектор", reason="Проверено по источнику", expected_version=0,
    ), db)
    db.refresh(run)
    assert run.result == raw_before
    assert view["result"]["checks"][0]["finding_status"] == "CONFIRMED_VIOLATION"
    assert view["version"] == 1
    with pytest.raises(HTTPException, match="протокол уже изменён"):
        official_api.decide(run.id, official_api.DecisionInput(
            finding_id="M-001:matrix", status="CANDIDATE", author="Инспектор",
            reason="Уточнение", expected_version=0,
        ), db)
    print("OK: решение создаёт новую версию и не переписывает машинный результат")


@pytest.mark.parametrize("status", ["CONFIRMED_VIOLATION", "NEGATIVE_VERIFIED"])
def test_final_expert_decision_requires_evidence(db, status):
    run = models.OfficialRun(
        object_id="synthetic", status="completed", result=complete_result(evidence=False),
        input_snapshot=[],
    )
    db.add(run)
    db.commit()
    with pytest.raises(HTTPException, match="полного комплекта доказательств"):
        official_api.decide(run.id, official_api.DecisionInput(
            finding_id="M-001:matrix", status=status, author="Инспектор",
            reason="Решение", expected_version=0,
        ), db)
    print("OK: окончательное решение без локализованного источника отклоняется")


def test_cancelled_queued_run_does_not_start(db, monkeypatch):
    run = models.OfficialRun(
        object_id="synthetic", status="queued", input_snapshot=[],
        cancelled_at=dt.datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    run_id = run.id
    factory = sessionmaker(bind=db.get_bind())
    monkeypatch.setattr(official_api, "SessionLocal", factory)
    monkeypatch.setattr(
        official_api, "run_official_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("запуск запрещён")),
    )

    official_api._execute(run_id)
    db.expire_all()
    stopped = db.get(models.OfficialRun, run_id)
    assert stopped.status == "cancelled"
    assert stopped.stage == "Остановлено до начала проверки"
    print("OK: отменённый в очереди прогон не начинает обработку")


def test_graphic_candidate_accepts_a_versioned_inspector_decision(db):
    result = complete_result()
    graphic = dict(result["checks"][0])
    graphic["finding_id"] = "graphic:1"
    graphic["parameter_code"] = "GRAPHIC"
    result["graphic_analysis"] = {
        "status": "completed", "reason": "", "candidates": [graphic], "performance": {},
    }
    run = models.OfficialRun(
        object_id="synthetic", status="completed", result=result, input_snapshot=[],
    )
    db.add(run)
    db.commit()

    view = official_api.decide(run.id, official_api.DecisionInput(
        finding_id="graphic:1", status="CONFIRMED_VIOLATION",
        author="Инспектор", reason="Проверено по листам", expected_version=0,
    ), db)
    candidate = view["result"]["graphic_analysis"]["candidates"][0]
    assert candidate["finding_status"] == "CONFIRMED_VIOLATION"
    assert candidate["review_history"][0]["author"] == "Инспектор"
    print("OK: решение инспектора версионируется и для графического кандидата")
