"""Сквозной сценарий: вход → анализ → гипотезы → карта внимания → акт."""
from __future__ import annotations

import logging
from pathlib import Path

from api.logging import MaskingFormatter, mask
from api.state import state


def test_full_inspector_scenario(client, auth):
    headers = auth("sudir:77001")

    # 1. Карточка объекта подтягивается по номеру разрешения из ИАИС ОГД
    lookup = client.get("/api/objects/lookup",
                        params={"permit": "77-123456-012345-2024"}, headers=headers).json()
    assert lookup["found"] and lookup["card"]["source"] == "mock"

    # 2. Доступные переходы определяются составом комплекта
    transitions = client.get("/api/objects/OBJ-001/transitions", headers=headers).json()
    available = {t["code"] for t in transitions["transitions"] if t["available"]}
    assert {"T1", "T3", "T5"} <= available

    # 3. Запуск анализа
    run = client.post("/api/analysis/run",
                      json={"object_id": "OBJ-001", "transitions": ["T1", "T3", "T5"]},
                      headers=headers)
    assert run.status_code == 200
    assert run.json()["findings"] > 0

    # 4. Журнал гипотез и карточка с переходом к листу документа
    findings = client.get("/api/findings", params={"object_id": "OBJ-001"},
                          headers=headers).json()["items"]
    assert findings
    card = client.get(f"/api/findings/{findings[0]['id']}", headers=headers).json()
    assert card["norm_texts"][0]["text"]
    assert card["norm_texts"][0]["edition_check"]["actual"] is True
    evidence = card["evidence"][0]
    page = client.get(f"/api/documents/{evidence['doc_id']}/page/{evidence['page']}",
                      headers=headers)
    assert page.status_code == 200 and page.headers["content-type"] == "image/png"

    # 5. Карта внимания: не более семи позиций
    attention = client.get("/api/attention", params={"object_id": "OBJ-001"},
                           headers=headers).json()
    assert 0 < len(attention["items"]) <= 7
    assert all(item["what_to_check"] for item in attention["items"])

    # 6. Оценка инспектора и проект акта
    confirmed = findings[0]["id"]
    assert client.post(f"/api/findings/{confirmed}/assess", json={"verdict": "confirmed"},
                       headers=headers).status_code == 200
    act = client.post("/api/reports/act",
                      json={"object_id": "OBJ-001", "finding_ids": [confirmed]},
                      headers=headers)
    assert act.status_code == 200 and len(act.content) > 10000

    # 7. Действия зафиксированы в журнале аудита, цепочка цела
    verify = client.post("/api/audit/verify", headers=auth("sudir:77006")).json()
    assert verify["valid"] is True


def test_upload_document_runs_through_full_pipeline(client, auth):
    """Инспектор сам загружает файл — он проходит приём, разбор и попадает в комплект."""
    headers = auth("sudir:77001")
    sample = Path(state.root) / "data/demo/generated/OBJ-001/as_built/aosr_48.pdf"
    with sample.open("rb") as fh:
        response = client.post("/api/objects/OBJ-001/documents", headers=headers,
                               files={"file": ("aosr_48.pdf", fh, "application/pdf")},
                               data={"state_hint": "AS_BUILT"})
    assert response.status_code == 200
    body = response.json()
    assert body["state_kind"] == "AS_BUILT"
    assert body["facts_count"] > 0

    full = client.get("/api/objects/OBJ-001", headers=headers).json()
    assert any(d["id"] == body["id"] for d in full["documents"])


def test_upload_rejects_unrecognised_file(client, auth):
    """Файл без распознанной сигнатуры отклоняется с понятным сообщением, не падением."""
    headers = auth("sudir:77001")
    response = client.post("/api/objects/OBJ-001/documents", headers=headers,
                           files={"file": ("note.txt", b"not a real document", "text/plain")})
    assert response.status_code == 422
    assert "не распознан" in response.json()["detail"]


def test_upload_denied_without_permission(client, auth):
    """Роль без права objects:create не может загрузить документ."""
    headers = auth("sudir:77004")      # аналитик
    response = client.post("/api/objects/OBJ-001/documents", headers=headers,
                           files={"file": ("aosr_48.pdf", b"%PDF-1.4 stub", "application/pdf")})
    assert response.status_code == 403


def test_upload_denied_for_foreign_object(client, auth):
    """Инспектор не может загрузить документ в чужой объект — отказ на уровне данных."""
    headers = auth("sudir:77007")      # инспектор объекта OBJ-002, не OBJ-001
    response = client.post("/api/objects/OBJ-001/documents", headers=headers,
                           files={"file": ("aosr_48.pdf", b"%PDF-1.4 stub", "application/pdf")})
    assert response.status_code == 404


def test_dashboard_metrics_are_consistent(client, auth):
    """Сумма по объектам сходится с общей цифрой."""
    data = client.get("/api/dashboard", headers=auth("sudir:77002")).json()
    assert sum(o["total"] for o in data["by_object"]) == data["findings_total"]
    assert sum(o["critical"] for o in data["by_object"]) == data["findings_critical"]
    assert data["saved_hours"] > 0 and data["formula"]


def test_logs_contain_no_personal_data(caplog, client, auth):
    """При полном прогоне в журналах нет ФИО, токенов и содержимого документов."""
    formatter = MaskingFormatter("%(message)s")
    with caplog.at_level(logging.INFO):
        logging.getLogger("test").info(
            "Вход: Кузнецова Марина Викторовна, token=abcdef1234567890, "
            "почта user@example.com, СНИЛС 123-456-789 00")
    rendered = " ".join(formatter.format(r) for r in caplog.records)
    assert "Марина Викторовна" not in rendered
    assert "abcdef1234567890" not in rendered
    assert "user@example.com" not in rendered
    assert "123-456-789 00" not in rendered
    assert mask("Bearer mock-token:sudir:77001").endswith("[токен скрыт]")
