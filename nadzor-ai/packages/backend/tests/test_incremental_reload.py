"""Инкрементальное обновление при дозагрузке (ТЗ 9.2).

Каждый параметр матрицы читает все три стадии, поэтому затронутые параметры
определяются составом актуальных редакций. Дозагрузка, которая его не
меняет, не должна снова гонять модель; которая меняет — пересчитывает всё.
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
        run.result = {"matrix_version": "1.1", "checks": [],
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
