"""Внешний контракт /api/v1 по ТЗ: загрузка → process_id → статус → протокол.

Сравнение документов здесь не запускается (фоновая проверка подменена):
проверяется то, что принимают по контракту, — приём файлов и реестра,
лимиты, статусы, дозагрузка, финализация и выгрузки.
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import api_v1, models, official_api  # noqa: E402
from app.db import (
    SessionLocal,  # noqa: E402
    get_session,  # noqa: E402
)
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def no_background_check(monkeypatch):
    monkeypatch.setattr(official_api, "_execute", lambda run_id: None)


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _registry(rows: list[dict]) -> tuple:
    return ("registry.json", json.dumps(rows).encode(), "application/json")


# file_id уникален на прогон: база тестов общая, а перезапись file_id другим
# содержимым сервис запрещает (перечень ИД).
_IDS: dict[str, str] = {}


def _row(name: str, stage: str, **extra) -> dict:
    for key in ("predecessor_id", "successor_id"):
        if key in extra:
            extra[key] = _IDS[extra[key]]
    _IDS[name] = f"{name}-{uuid.uuid4().hex[:8]}"
    return {"file_id": _IDS[name], "file_name": name, "object_id": "OBJ-1",
            "doc_stage": stage, "discipline": "АР", "document_code": f"CODE-{stage}",
            "revision": "1", "approval_status": "APPROVED", "approval_date": "2026-01-01",
            **extra}


def _upload(files: list[tuple[str, bytes]], registry=None, **form):
    parts = [("files", (name, data, "application/pdf")) for name, data in files]
    if registry is not None:
        parts.append(("registry", registry))
    return client.post("/api/v1/documents/upload", files=parts, data=form)


def test_upload_with_registry_returns_process_id_and_pending():
    pd, rd = _pdf("PD text"), _pdf("RD text")
    response = _upload([("pd.pdf", pd), ("rd.pdf", rd)],
                       _registry([_row("pd.pdf", "PD"), _row("rd.pdf", "RD")]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["scenario"] == "PD_RD_ONLY"
    assert body["upload_status"] == {"PD": "PD_UPLOADED", "RD": "RD_UPLOADED",
                                     "ID": "ID_MISSING"}
    status = client.get(f"/api/v1/processes/{body['process_id']}/status").json()
    assert status["status"] == "PENDING" and status["process_id"] == body["process_id"]


def test_package_without_registry_is_accepted_as_clarification_required():
    body = _upload([("x.pdf", _pdf("x"))]).json()
    process = client.get(f"/api/v1/processes/{body['process_id']}").json()
    statuses = {item["completeness_status"] for item in process["result"]["checks"]}
    assert statuses == {"CLARIFICATION_REQUIRED"}
    assert "реестр" in process["result"]["checks"][0]["explanation"]


def test_rejections_name_the_reason(monkeypatch):
    monkeypatch.setattr(api_v1, "MAX_FILE_BYTES", 2000)
    big = _pdf("x" * 10) + b"0" * 5000
    good = _pdf("good")
    response = _upload(
        [("big.pdf", big), ("note.docx", b"PK\x03\x04docx"), ("ghost.pdf", _pdf("g")),
         ("bad.pdf", _pdf("bad")), ("good.pdf", good)],
        _registry([_row("big.pdf", "PD"), _row("note.docx", "PD"),
                   _row("bad.pdf", "RD", sha256="0" * 64), _row("good.pdf", "PD")]))
    reasons = {item["file_name"]: item["reason"] for item in response.json()["rejected"]}
    assert "МБ" in reasons["big.pdf"]
    assert "формат" in reasons["note.docx"] and "PDF" in reasons["note.docx"]
    assert "реестре" in reasons["ghost.pdf"]
    assert "SHA-256" in reasons["bad.pdf"]
    assert [item["file_name"] for item in response.json()["accepted"]] == ["good.pdf"]


def test_package_limit_rejects_the_whole_package(monkeypatch):
    monkeypatch.setattr(api_v1, "MAX_PACKAGE_BYTES", 100)
    response = _upload([("a.pdf", _pdf("a"))])
    assert response.status_code == 413


def test_registry_links_revisions_by_registry_ids():
    old, new = _pdf("old"), _pdf("new")
    body = _upload([("old.pdf", old), ("new.pdf", new)], _registry([
        _row("old.pdf", "PD", approval_status="SUPERSEDED"),
        _row("new.pdf", "PD", predecessor_id="old.pdf")])).json()
    ids = {item["file_name"]: item["document_id"] for item in body["accepted"]}
    db = next(get_session())
    try:
        new_doc = db.get(models.Document, ids["new.pdf"])
        assert new_doc.source_metadata["predecessor_id"] == ids["old.pdf"]
        assert new_doc.digest == hashlib.sha256(new).hexdigest()
    finally:
        db.close()


def _completed(process_id: int, finding_status: str) -> None:
    db = next(get_session())
    try:
        run = db.get(models.OfficialRun, process_id)
        run.status = "completed"
        run.result = {"matrix_version": "1.1", "checks": [{
            "finding_id": "M-001:matrix", "parameter_code": "M-001", "priority": "HIGH",
            "finding_status": finding_status, "completeness_status": "COMPLETE",
            "technical_status": "completed", "expected_value": "1", "actual_value": "2",
            "explanation": "x", "evidence": [{"document_id": 1, "stage": "PD",
                                              "bbox": [0, 0, 1, 1], "page": 1}]}],
            "coverage": {"total": 1, "completed": 1, "not_run": 0},
            "graphic_analysis": {"status": "not_run", "candidates": []},
            "document_selection": {"selected": {}, "problems": {}}}
        db.commit()
    finally:
        db.close()


def test_reload_keeps_the_previous_protocol_version_and_finalize_locks_it(monkeypatch):
    body = _upload([("pd.pdf", _pdf("p"))], _registry([_row("pd.pdf", "PD")])).json()
    pid = body["process_id"]
    _completed(pid, "CANDIDATE")
    assert client.get(f"/api/v1/processes/{pid}/status").json()["status"] == "READY"

    reload = _upload([("rd.pdf", _pdf("r"))], _registry([_row("rd.pdf", "RD")]),
                     process_id=str(pid)).json()
    assert reload["status"] == "PENDING" and reload["scenario"] == "PD_RD_ONLY"
    process = client.get(f"/api/v1/processes/{pid}").json()
    assert process["protocol_version"] == 2

    _completed(pid, "CANDIDATE")
    decision = client.post(f"/api/v1/processes/{pid}/decisions", json={
        "finding_id": "M-001:matrix", "status": "CONFIRMED_VIOLATION", "author": "insp",
        "reason": "подтверждено", "expected_version": 0})
    assert decision.json()["status"] == "COMPLETED", decision.text

    early = client.post(f"/api/v1/inspection/{pid}")
    assert early.status_code == 409

    final = client.post(f"/api/v1/processes/{pid}/finalize", json={"author": "insp"}).json()
    assert final["status"] == "FINALIZED"
    # ТЗ 9.6: документы в финализированный протокол принимаются, но проверку не
    # запускают — инспектор получает уведомление.
    late = _upload([("id.pdf", _pdf("i"))], _registry([_row("id.pdf", "ID")]),
                   process_id=str(pid))
    assert late.status_code == 200, late.text
    assert late.json()["status"] == "FINALIZED" and "notice" in late.json()
    after = client.get(f"/api/v1/processes/{pid}").json()
    assert after["status"] == "FINALIZED" and after["protocol_version"] == 2
    assert [item["metadata"]["stage"] for item in after["pending_documents"]] == ["ID"]

    sent = client.post(f"/api/v1/inspection/{pid}").json()
    assert sent["sync_status"] == "LOCAL_ONLY"
    assert [c["finding_id"] for c in sent["confirmed_violations"]] == ["M-001:matrix"]
    assert {f["stage"] for f in sent["input_files"]} == {"PD", "RD"}

    # Передача включается адресом приёма; результат виден в статусе процесса.
    from app import external_sync
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")
    received = []

    def fake_post(url, json, headers, timeout):
        received.append((json, headers))
        return external_sync.httpx.Response(202, request=external_sync.httpx.Request("POST", url))

    monkeypatch.setattr(external_sync.httpx, "post", fake_post)
    delivered = client.post(f"/api/v1/inspection/{pid}").json()
    assert delivered["sync_status"] == "SENT", delivered
    assert received[0][0]["confirmed_violations"] == sent["confirmed_violations"]
    assert received[0][1]["Idempotency-Key"] == f"nadzor-{pid}-v2"
    assert client.get(f"/api/v1/processes/{pid}/status").json()["sync_status"] == "SENT"


@pytest.mark.parametrize("fmt,magic", [("json", b"{"), ("xml", b"<?xml"),
                                       ("docx", b"PK"), ("pdf", b"%PDF")])
def test_protocol_exports_in_every_format(fmt, magic):
    pid = _upload([("pd.pdf", _pdf("p"))], _registry([_row("pd.pdf", "PD")])).json()["process_id"]
    _completed(pid, "CANDIDATE")
    response = client.get(f"/api/v1/processes/{pid}/export?format={fmt}")
    assert response.status_code == 200, response.text
    assert response.content.startswith(magic)


def _xlsx(rows: list[dict]) -> bytes:
    """Минимальный XLSX с реестром (inline-строки), как его сохраняет табличный редактор."""
    import io
    import zipfile

    header = list(rows[0])

    def cell(ref: str, value: str) -> str:
        text = str(value).replace("&", "&amp;").replace("<", "&lt;")
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    def col(index: int) -> str:
        return chr(ord("A") + index)

    lines = []
    for number, values in enumerate([header] + [[row[key] for key in header] for row in rows],
                                    start=1):
        cells = "".join(cell(f"{col(i)}{number}", value) for i, value in enumerate(values))
        lines.append(f'<row r="{number}">{cells}</row>')
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(lines)}</sheetData></worksheet>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return stream.getvalue()


def test_registry_is_accepted_as_xlsx_with_discipline_and_file_id():
    rows = [_row("x-pd.pdf", "PD"), _row("x-rd.pdf", "RD")]
    body = _upload([("x-pd.pdf", _pdf("xp")), ("x-rd.pdf", _pdf("xr"))],
                   ("registry.xlsx", _xlsx(rows),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    assert body.status_code == 200, body.text
    assert len(body.json()["accepted"]) == 2
    with SessionLocal() as db:
        doc = db.get(models.Document, body.json()["accepted"][0]["document_id"])
        assert doc.source_metadata["discipline"] == "АР"
        assert doc.source_metadata["file_id"] == rows[0]["file_id"]
    print("OK: реестр в XLSX принят; раздел и file_id сохранены (перечень ИД)")


def test_registry_without_mandatory_fields_is_refused():
    row = _row("y.pdf", "PD")
    del row["discipline"]
    response = _upload([("y.pdf", _pdf("y"))], _registry([row]))
    assert response.status_code == 422 and "discipline" in response.text
    print("OK: реестр без обязательного поля отклонён с названием поля")


def test_successor_id_links_revisions_like_predecessor_id():
    old = _row("s-old.pdf", "PD", approval_status="SUPERSEDED")
    new = _row("s-new.pdf", "PD")
    old["successor_id"] = new["file_id"]
    body = _upload([("s-old.pdf", _pdf("so")), ("s-new.pdf", _pdf("sn"))],
                   _registry([old, new])).json()
    ids = {item["file_name"]: item["document_id"] for item in body["accepted"]}
    with SessionLocal() as db:
        assert db.get(models.Document, ids["s-new.pdf"]).source_metadata["predecessor_id"] \
            == ids["s-old.pdf"]
    print("OK: связь редакций задаётся и successor_id заменённой")


def test_file_id_cannot_be_overwritten_with_other_content():
    row = _row("w.pdf", "PD")
    content = _pdf("first")
    first = _upload([("w.pdf", content)], _registry([row]))
    assert first.status_code == 200
    again = _upload([("w.pdf", content)], _registry([row]))
    assert again.status_code == 200, "тот же файл повторно допустим"
    other = _upload([("w.pdf", _pdf("changed"))], _registry([row]))
    assert other.status_code == 422 and "перезапись запрещена" in other.text
    print("OK: другое содержимое под прежним file_id отклоняется (перечень ИД)")
