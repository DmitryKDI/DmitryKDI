"""Вход по логину и паролю, роли и журнал аудита (ТЗ 12, п.1, 2, 4)."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import auth, models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

pytestmark = pytest.mark.real_auth
PASSWORD = "correct-horse-1"  # noqa: S105 — пароль тестовой учётной записи


def _user(role: str, *, password: str = PASSWORD, active: bool = True) -> str:
    login = f"{role}-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(models.User(login=login, password_hash=auth.hash_password(password), role=role,
                           full_name=f"Пользователь {role}", is_active=active))
        db.commit()
    return login


def _client(role: str) -> tuple[TestClient, str]:
    client = TestClient(app)
    login = _user(role)
    response = client.post("/api/v1/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client, login


def test_everything_except_login_and_health_requires_a_session():
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/processes/1/status").status_code == 401
    assert client.get("/official/runs").status_code == 401
    assert client.get("/documents").status_code == 401
    assert client.get("/page-image/1/1").status_code == 401
    print("OK: без входа доступны только вход и проверка работоспособности")


def test_password_is_stored_as_a_salted_hash():
    stored = auth.hash_password(PASSWORD)
    assert PASSWORD not in stored and stored.startswith("scrypt$")
    assert stored != auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password("wrong-password", stored)
    print("OK: пароль хранится хешем scrypt с солью")


def test_wrong_password_is_refused_and_logged():
    login = _user("inspector")
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": login, "password": "nope-nope"})
    assert response.status_code == 401
    with SessionLocal() as db:
        row = (db.query(models.AuditLog).filter_by(login=login)
               .order_by(models.AuditLog.id.desc()).first())
    assert row is not None and row.status_code == 401
    assert row.action == "POST /api/v1/auth/login"
    print("OK: неудачный вход отклонён и записан в журнал аудита")


def test_bearer_token_and_cookie_both_open_the_session():
    client, login = _client("inspector")
    token = client.post("/api/v1/auth/login",
                        json={"login": login, "password": PASSWORD}).json()["token"]
    assert client.get("/api/v1/auth/me").json()["login"] == login
    bare = TestClient(app)
    me = bare.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["role"] == "inspector"
    bare.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert bare.get("/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {token}"}).status_code == 401
    print("OK: сессия открывается токеном и cookie, выход её закрывает")


def test_roles_limit_what_a_user_can_do():
    inspector, _ = _client("inspector")
    assert inspector.get("/api/v1/admin/users").status_code == 403
    assert inspector.post("/official/runs/999999/unfinalize",
                          json={"reason": "x", "role": "admin"}).status_code == 403
    assert inspector.put("/settings", json={"provider": "local"}).status_code == 403

    service, _ = _client("service")
    assert service.post("/official/runs/999999/decisions", json={
        "finding_id": "x", "status": "CANDIDATE", "reason": "x",
        "expected_version": 0}).status_code == 403

    ml, _ = _client("ml_engineer")
    assert ml.get("/api/v1/admin/audit?limit=1").status_code == 200
    assert ml.post("/documents?side=before").status_code == 403
    print("OK: роли инспектора, внешней системы и ML-инженера разграничены")


def test_admin_manages_users_and_disabling_ends_sessions():
    admin, _ = _client("admin")
    login = f"new-{uuid.uuid4().hex[:8]}"
    short = admin.post("/api/v1/admin/users",
                       json={"login": login, "password": "short", "role": "inspector"})
    assert short.status_code == 422
    created = admin.post("/api/v1/admin/users", json={
        "login": login, "password": PASSWORD, "role": "inspector", "full_name": "И. И."}).json()
    user_client = TestClient(app)
    assert user_client.post("/api/v1/auth/login",
                            json={"login": login, "password": PASSWORD}).status_code == 200
    admin.patch(f"/api/v1/admin/users/{created['id']}", json={"is_active": False})
    assert user_client.get("/api/v1/auth/me").status_code == 401
    print("OK: администратор заводит и отключает пользователей; отключение закрывает сессии")


def test_every_change_is_audited_with_user_ip_and_agent():
    admin, login = _client("admin")
    admin.post("/api/v1/admin/users", json={
        "login": f"a-{uuid.uuid4().hex[:8]}", "password": PASSWORD, "role": "inspector"},
        headers={"User-Agent": "audit-test", "X-Forwarded-For": "10.1.2.3"})
    rows = admin.get(f"/api/v1/admin/audit?user={login}&action=/admin/users").json()
    assert rows, "действие не попало в журнал"
    assert rows[0]["ip_address"] == "10.1.2.3"
    assert rows[0]["user_agent"] == "audit-test"
    assert rows[0]["user_id"] is not None and rows[0]["status_code"] == 200
    print("OK: действие записано с пользователем, IP-адресом и агентом")
