"""Контракт асинхронной pull-модели: запуск отдаёт process_id, статус
опрашивается по нему, процесс можно остановить.

Проверка организаторов идёт по контракту, а не по интерфейсу: имя
идентификатора и путь опроса — часть требования, а не деталь реализации.
"""
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import official_api  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


def _document(tmp_path, name: str, stage: str) -> int:
    pdf = tmp_path / name
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Text")
    doc.save(pdf)
    doc.close()
    with pdf.open("rb") as handle:
        doc_id = client.post("/documents?side=before",
                             files={"file": (name, handle, "application/pdf")}).json()["id"]
    response = client.put(f"/official/documents/{doc_id}/metadata", json={
        "object_id": "OBJ-1", "stage": stage, "document_code": f"CODE-{stage}",
        "revision": "1", "approval_status": "DRAFT",
    })
    assert response.status_code == 200, response.text
    return doc_id


def test_process_is_started_and_polled_by_process_id(tmp_path, monkeypatch):
    # Сама проверка здесь не нужна: проверяется контракт, а не разбор.
    monkeypatch.setattr(official_api, "_execute", lambda run_id: None)
    ids = [_document(tmp_path, "pd.pdf", "PD"), _document(tmp_path, "rd.pdf", "RD")]

    started = client.post("/official/processes",
                          json={"object_id": "OBJ-1", "document_ids": ids})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["status"] == "queued"
    process_id = body["process_id"]

    polled = client.get(f"/official/processes/{process_id}").json()
    assert polled["process_id"] == process_id
    assert polled["status"] == "queued"

    cancelled = client.post(f"/official/processes/{process_id}/cancel").json()
    assert cancelled["process_id"] == process_id
    assert client.get("/official/processes/999999").status_code == 404


def test_openapi_names_the_path_parameter_process_id():
    schema = client.get("/openapi.json").json()
    assert schema["openapi"].startswith("3.")
    parameters = schema["paths"]["/official/processes/{process_id}"]["get"]["parameters"]
    assert [item["name"] for item in parameters] == ["process_id"]
