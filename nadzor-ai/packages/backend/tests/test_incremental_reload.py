"""Инкрементальное обновление при дозагрузке (ТЗ 9.2).

Дозагрузка, которая не меняет состав актуальных редакций, не гоняет модель.
Которая меняет — пересчитывает только параметры, для которых появились новые
данные (и неполные); остальные результаты и решения инспектора переносятся.
"""
import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, official_api  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.parameter_catalog import current_matrix_version, list_parameters  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _upload(name: str, stage: str, approval: str, process_id=None) -> dict:
    registry = [{"file_id": name, "file_name": name, "object_id": "OBJ-INC",
                 "doc_stage": stage, "document_code": f"C-{stage}-{name}", "revision": "1",
                 "approval_status": approval, "approval_date": "2026-01-01"}]
    data = {"process_id": str(process_id)} if process_id else {}
    return client.post("/api/v1/documents/upload", files=[
        ("files", (name, _pdf(name), "application/pdf")),
        ("registry", ("r.json", json.dumps(registry).encode(), "application/json")),
    ], data=data).json()


def _complete_with_current_selection(process_id: int) -> None:
    db = next(get_session())
    try:
        run = db.get(models.OfficialRun, process_id)
        ids = [item["id"] for item in run.input_snapshot]
        run.status = "completed"
        run.result = {"matrix_version": current_matrix_version(), "checks": [],
                      "coverage": {"total": 0, "completed": 0, "not_run": 0},
                      "graphic_analysis": {"status": "not_run", "candidates": []},
                      "document_selection": {"selected": {"PD": ids}, "problems": {}}}
        db.commit()
    finally:
        db.close()


@pytest.fixture
def deferred(monkeypatch):
    """Фоновая проверка не запускается сама — тест вызывает её явно."""
    real = official_api._execute
    monkeypatch.setattr(official_api, "_execute", lambda run_id: None)
    return real


def test_reload_without_new_current_revision_reuses_the_protocol(deferred, monkeypatch):
    pid = _upload("pd.pdf", "PD", "APPROVED")["process_id"]
    _complete_with_current_selection(pid)
    _upload("rd-old.pdf", "RD", "SUPERSEDED", process_id=pid)
    monkeypatch.setattr(official_api, "run_official_analysis",
                        lambda *a, **kw: pytest.fail("модель не должна вызываться"))

    deferred(pid)

    body = client.get(f"/api/v1/processes/{pid}").json()
    assert body["run_state"] == "completed"
    assert body["result"]["incremental"]["reused_from_version"] == 1
    assert body["protocol_version"] == 2


def test_reload_with_a_new_current_revision_recomputes(deferred, monkeypatch):
    pid = _upload("pd2.pdf", "PD", "APPROVED")["process_id"]
    _complete_with_current_selection(pid)
    _upload("rd2.pdf", "RD", "APPROVED", process_id=pid)
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        return {"matrix_version": "1.1", "checks": [],
                "coverage": {"total": 0, "completed": 0, "not_run": 0},
                "graphic_analysis": {"status": "not_run", "candidates": []},
                "document_selection": {"selected": {}, "problems": {}}}

    monkeypatch.setattr(official_api, "run_official_analysis", fake)
    deferred(pid)

    body = client.get(f"/api/v1/processes/{pid}").json()
    assert calls == [1]
    assert body["result"]["incremental"]["recomputed"] == "all"


def test_cancel_checks_from_worker_threads_do_not_break_the_run(deferred, monkeypatch):
    """Сверка параллельна: проверка остановки идёт из рабочих потоков, пока
    основной пишет прогресс. Общая сессия ломала прогон посреди работы
    ошибкой «строка удалена» — воспроизведено на составе в Docker."""
    import threading

    pid = _upload("pd3.pdf", "PD", "APPROVED")["process_id"]
    _upload("rd3.pdf", "RD", "APPROVED", process_id=pid)

    def fake(documents, config, *, progress, cancelled, **kwargs):
        stop = threading.Event()
        errors = []

        def worker():
            while not stop.is_set():
                try:
                    cancelled()
                except Exception as exc:  # noqa: BLE001 — ошибку и проверяем
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for step in range(200):
            progress("Сверка параметров матрицы", step, 200)
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
        # Общая сессия на старом коде не только падала, но и зависала —
        # тест должен упасть по таймауту, а не висеть.
        assert not any(thread.is_alive() for thread in threads), "потоки зависли"
        assert not errors, errors[:3]
        return {"matrix_version": "1.1", "checks": [],
                "coverage": {"total": 0, "completed": 0, "not_run": 0},
                "graphic_analysis": {"status": "not_run", "candidates": []},
                "document_selection": {"selected": {}, "problems": {}}}

    monkeypatch.setattr(official_api, "run_official_analysis", fake)
    deferred(pid)
    assert client.get(f"/api/v1/processes/{pid}").json()["run_state"] == "completed"


def _complete_check(parameter: dict, document_id: int) -> dict:
    return {"finding_id": f"{parameter['code']}:matrix", "parameter_code": parameter["code"],
            "parameter_name": parameter["name"], "technical_status": "completed",
            "completeness_status": "COMPLETE", "finding_status": "CANDIDATE",
            "evidence": [{"stage": "PD", "document_id": document_id, "page": 1,
                          "bbox": [0, 0, 1, 1]}]}


def test_reload_recomputes_only_parameters_with_new_data(deferred, monkeypatch):
    parameters = list_parameters()
    target = parameters[4]
    word = max((w for w in target["name"].split() if len(w) >= 4), key=len)
    pid = _upload("pd4.pdf", "PD", "APPROVED")["process_id"]
    db = next(get_session())
    try:
        run = db.get(models.OfficialRun, pid)
        pd_id = run.input_snapshot[0]["id"]
        run.status = "completed"
        run.result = {"matrix_version": current_matrix_version(),
                      "checks": [_complete_check(item, pd_id) for item in parameters],
                      "coverage": {"total": len(parameters), "completed": len(parameters),
                                   "not_run": 0},
                      "graphic_analysis": {"status": "not_run", "candidates": []},
                      "document_selection": {"selected": {"PD": [pd_id]}, "problems": {}}}
        db.commit()
    finally:
        db.close()
    untouched = next(item for item in parameters
                     if not any(len(w) >= 4 and w.casefold() in word.casefold()
                                for w in item["name"].split()))
    for finding in (f"{target['code']}:matrix", f"{untouched['code']}:matrix"):
        version = client.get(f"/api/v1/processes/{pid}").json()["version"]
        client.post(f"/official/runs/{pid}/decisions", json={
            "finding_id": finding, "status": "CONFIRMED_VIOLATION", "reason": "да",
            "expected_version": version})

    # Новый документ содержит слово из наименования только одного параметра.
    # Текст страницы подставляется напрямую: встроенный шрифт PDF не пишет
    # кириллицу, а проверяется здесь выбор параметров, а не распознавание.
    _upload("rd4.pdf", "RD", "APPROVED", process_id=pid)
    from app import official_pipeline
    monkeypatch.setattr(official_pipeline, "_facts", lambda document: [
        {"page": 1, "text": f"Таблица: {word} 12"}])
    seen = {}

    def fake(documents, config, *, codes=None, **kwargs):
        seen["codes"] = set(codes)
        return {"matrix_version": current_matrix_version(),
                "checks": [{**_complete_check(item, pd_id), "explanation": "новый"}
                           for item in parameters if item["code"] in codes],
                "coverage": {}, "graphic_analysis": {"status": "not_run", "candidates": []},
                "document_selection": {"selected": {}, "problems": {}}}

    monkeypatch.setattr(official_api, "run_official_analysis", fake)
    deferred(pid)

    body = client.get(f"/api/v1/processes/{pid}").json()
    assert target["code"] in seen["codes"]
    assert untouched["code"] not in seen["codes"]
    assert len(seen["codes"]) < len(parameters)
    checks = {item["parameter_code"]: item for item in body["result"]["checks"]}
    assert len(checks) == len(parameters)
    assert checks[untouched["code"]]["finding_status"] == "CONFIRMED_VIOLATION", \
        "решение по незатронутому параметру потеряно"
    assert checks[target["code"]]["finding_status"] == "CANDIDATE", \
        "решение по прежнему результату перенесено на новый"
    assert body["result"]["incremental"]["recomputed"] == sorted(seen["codes"])
    print("OK: пересчитаны только параметры с новыми данными; прочие решения сохранены")
