"""Разграничение доступа: проверка выполняется на уровне данных."""
from __future__ import annotations


def test_foreign_object_is_denied(client, auth):
    """Инспектор другого отдела не получает объект даже по прямой ссылке."""
    stranger = auth("sudir:77007")
    response = client.get("/api/objects/OBJ-001", headers=stranger)
    assert response.status_code == 404
    assert "OBJ-001" not in response.text or "не найден" in response.text


def test_own_object_is_available(client, auth):
    assert client.get("/api/objects/OBJ-001", headers=auth("sudir:77001")).status_code == 200


def test_list_is_scoped_by_assignment(client, auth):
    """В журнале объектов инспектор видит только свои объекты."""
    items = client.get("/api/objects", headers=auth("sudir:77001")).json()["items"]
    assert items and all(o["assigned_to"] == "sudir:77001" for o in items)


def test_findings_of_foreign_object_are_hidden(client, auth):
    findings = client.get("/api/findings", params={"object_id": "OBJ-001"},
                          headers=auth("sudir:77007")).json()
    assert findings["total"] == 0


def test_auditor_has_no_access_to_materials(client, auth):
    """Аудитор читает журнал, но не содержимое проверок."""
    auditor = auth("sudir:77006")
    assert client.get("/api/objects", headers=auditor).status_code == 403
    assert client.get("/api/audit", headers=auditor).status_code == 200


def test_inspector_cannot_read_audit_log(client, auth):
    assert client.get("/api/audit", headers=auth("sudir:77001")).status_code == 403


def test_inspector_cannot_change_configuration(client, auth):
    response = client.post("/api/admin/providers/default", json={"name": "mock"},
                           headers=auth("sudir:77001"))
    assert response.status_code == 403


def test_anonymous_request_is_rejected(client):
    assert client.get("/api/objects").status_code == 401
    assert client.get("/api/findings").status_code == 401


def test_revoked_account_loses_access(client, auth):
    """Отзыв прав в системе идентификации закрывает доступ."""
    from api.state import state
    headers = auth("sudir:77004")
    assert client.get("/api/objects", headers=headers).status_code == 200
    state.identity.revoke("sudir:77004")
    try:
        assert client.get("/api/objects", headers=headers).status_code == 401
    finally:
        state.identity._revoked.discard("sudir:77004")
