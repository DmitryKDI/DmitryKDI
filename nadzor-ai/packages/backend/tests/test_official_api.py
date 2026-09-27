"""Версии официального протокола и решения инспектора не меняют машинный снимок."""

import datetime as dt

import pytest
from app import main as backend_main
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


def stored_document(db, doc_id: int, *, stage: str, code: str, predecessor_id=None):
    row = models.Document(
        id=doc_id, name=f"{code}.pdf", side="before" if stage == "PD" else "after",
        file_path=f"/{code}.pdf", pages=1, status="ok", digest=f"digest-{doc_id}",
        source_metadata={
            "object_id": "synthetic", "stage": stage, "document_code": code,
            "revision": "R", "approval_status": "APPROVED",
            "approval_date": "2026-01-01", "predecessor_id": predecessor_id,
        },
    )
    db.add(row)
    db.commit()
    return row


def metadata(*, stage: str, code: str, predecessor_id=None):
    return official_api.MetadataInput(
        object_id="synthetic", stage=stage, document_code=code, revision="R2",
        approval_status="APPROVED", approval_date=dt.date(2026, 2, 1),
        predecessor_id=predecessor_id,
    )


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
    assert view["object_id"] == "synthetic"
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
            reason="Решение", expected_version=0, reason_code="APPROVED_CHANGE",
        ), db)
    print("OK: окончательное решение без локализованного источника отклоняется")


def test_request_for_clarification_is_versioned_without_complete_evidence(db):
    run = models.OfficialRun(
        object_id="synthetic", status="completed", result=complete_result(evidence=False),
        input_snapshot=[],
    )
    db.add(run)
    db.commit()
    view = official_api.decide(run.id, official_api.DecisionInput(
        finding_id="M-001:matrix", status="CLARIFICATION_REQUIRED",
        author="Инспектор", reason="Нужен читаемый лист", expected_version=0,
    ), db)
    check = view["result"]["checks"][0]
    assert check["finding_status"] == "CLARIFICATION_REQUIRED"
    assert check["review_history"][0]["reason"] == "Нужен читаемый лист"
    print("OK: запрос уточнения сохраняется версией без ложного финального решения")


@pytest.mark.parametrize(
    ("stage", "code", "message"),
    [("RD", "PD-A", "разным стадиям"), ("PD", "PD-B", "разные шифры")],
)
def test_revision_predecessor_must_match_stage_and_document_code(db, stage, code, message):
    stored_document(db, 1, stage="PD", code="PD-A")
    stored_document(db, 2, stage=stage, code=code)
    with pytest.raises(HTTPException, match=message):
        official_api.save_metadata(2, metadata(
            stage=stage, code=code, predecessor_id=1,
        ), db)
    print("OK: цепочка редакций не связывает разные стадии или шифры")


def test_revision_predecessor_cannot_form_a_cycle(db):
    stored_document(db, 1, stage="PD", code="PD-A", predecessor_id=2)
    stored_document(db, 2, stage="PD", code="PD-A")
    with pytest.raises(HTTPException, match="содержит цикл"):
        official_api.save_metadata(2, metadata(
            stage="PD", code="PD-A", predecessor_id=1,
        ), db)
    print("OK: циклическая цепочка редакций отклоняется")


def test_official_document_can_be_deleted_after_metadata_was_saved(db, monkeypatch):
    stored_document(db, 1, stage="PD", code="PD-A")
    official_api.save_metadata(1, metadata(stage="PD", code="PD-A"), db)
    monkeypatch.setattr(backend_main.file_store, "forget", lambda _digest: True)
    assert backend_main.delete_document(1, db) == {"ok": True}
    assert db.get(models.Document, 1) is None
    assert db.query(models.DocumentMetadataEvent).count() == 0
    print("OK: кнопка удаляет официальный документ вместе с историей его карточки")


def test_document_used_by_an_official_protocol_cannot_be_deleted(db):
    stored_document(db, 1, stage="PD", code="PD-A")
    db.add(models.OfficialRun(
        object_id="synthetic", status="completed",
        input_snapshot=[{"id": 1, "digest": "digest-1", "metadata": {}}],
        result=complete_result(),
    ))
    db.commit()
    with pytest.raises(HTTPException, match="официальный протокол"):
        backend_main.delete_document(1, db)
    assert db.get(models.Document, 1) is not None
    print("OK: источник сохранённого протокола нельзя удалить и лишить доказательства")


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
