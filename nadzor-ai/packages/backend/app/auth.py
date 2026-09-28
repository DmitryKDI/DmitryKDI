"""Аутентификация и разграничение прав (ТЗ 12, п.1–2).

Вход по логину и паролю для всех категорий пользователей. Роли:

  inspector   — просмотр документов и протоколов, верификация, финализация;
  supervisor  — инспектор с правом супервизора: дополнительно отмена финализации;
  admin       — всё, включая управление пользователями, параметрами матрицы и
                нормативной базой;
  ml_engineer — доступ к журналам и данным дообучения;
  service     — учётная запись внешней системы (ИАИС «РиН»): загрузка
                комплекта и получение результатов по /api/v1.

Пароль хранится только хешем scrypt с солью. Сессия — случайный токен; в базе
лежит его SHA-256, поэтому утечка базы не даёт действующих токенов. Токен
передаётся заголовком `Authorization: Bearer` (внешние системы) или
HttpOnly-cookie (интерфейс: изображение листа загружается тегом <img>, которому
заголовок не передать).
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal, get_session

ROLES = ("inspector", "supervisor", "admin", "ml_engineer", "service")
ROLE_TITLES = {
    "inspector": "Инспектор", "supervisor": "Инспектор-супервизор",
    "admin": "Администратор", "ml_engineer": "ML-инженер", "service": "Внешняя система",
}
# Кто может проверять и принимать решения по протоколу.
VERIFIERS = ("inspector", "supervisor")
# Кто может читать протоколы и документы, в том числе внешняя система.
READERS = ("inspector", "supervisor", "ml_engineer", "service")

COOKIE_NAME = "inspector_session"
# Сколько живёт сессия без повторного входа, часов: рабочая смена инспектора.
SESSION_TTL_HOURS = 12
# Минимальная длина пароля — политика доступа, а не порог правды.
MIN_PASSWORD_LENGTH = 8
# Параметры scrypt: стоимость перебора пароля при утечке базы.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

# Учётная запись по умолчанию на время проверки: admin / admin. Переменные
# NADZOR_ADMIN_LOGIN и NADZOR_ADMIN_PASSWORD её заменяют; в промышленном
# контуре пароль задаётся окружением или меняется сразу после первого входа.
DEFAULT_ADMIN_LOGIN = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"  # noqa: S105 — известный пароль стенда проверки, не секрет


@dataclass(frozen=True)
class Principal:
    user_id: int
    login: str
    role: str
    full_name: str = ""

    @property
    def display(self) -> str:
        return self.full_name or self.login


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    salt_b64 = base64.b64encode(salt).decode()
    digest_b64 = base64.b64encode(digest).decode()
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_b64}${digest_b64}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"пароль короче {MIN_PASSWORD_LENGTH} символов")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: models.User) -> str:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.utcnow()
    db.add(models.AuthSession(token_hash=_token_hash(token), user_id=user.id,
                              created_at=now,
                              expires_at=now + dt.timedelta(hours=SESSION_TTL_HOURS)))
    user.last_login_at = now
    db.commit()
    return token


def drop_session(db: Session, token: str) -> None:
    db.query(models.AuthSession).filter_by(token_hash=_token_hash(token)).delete()
    db.commit()


def request_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(COOKIE_NAME, "")


def current_user(request: Request, db: Session = Depends(get_session)) -> Principal:
    """Пользователь запроса; без действующей сессии — 401."""
    token = request_token(request)
    if not token:
        raise HTTPException(401, "требуется вход в систему")
    row = db.query(models.AuthSession).filter_by(token_hash=_token_hash(token)).first()
    if row is None or row.expires_at < dt.datetime.utcnow():
        raise HTTPException(401, "сессия истекла, войдите заново")
    user = db.get(models.User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "учётная запись отключена")
    principal = Principal(user.id, user.login, user.role, user.full_name or "")
    # Для журнала аудита: кто выполнил действие (ТЗ 12, п.4).
    request.state.principal = principal
    return principal


def require(*roles: str):
    """Зависимость FastAPI: пользователь с одной из ролей; администратор — всегда."""
    allowed = set(roles)

    def dependency(user: Principal = Depends(current_user)) -> Principal:
        if user.role != "admin" and user.role not in allowed:
            raise HTTPException(403, "недостаточно прав для этого действия")
        return user

    return dependency


def ensure_initial_admin() -> None:
    """Первый запуск: создать администратора, если пользователей нет.

    Логин и пароль — из NADZOR_ADMIN_LOGIN / NADZOR_ADMIN_PASSWORD, иначе
    admin / admin. Если пользователи уже есть, переменные не читаются.
    """
    db = SessionLocal()
    try:
        if db.query(models.User).count():
            return
        login = os.environ.get("NADZOR_ADMIN_LOGIN", "").strip() or DEFAULT_ADMIN_LOGIN
        password = os.environ.get("NADZOR_ADMIN_PASSWORD", "") or DEFAULT_ADMIN_PASSWORD
        db.add(models.User(login=login, password_hash=hash_password(password), role="admin",
                           full_name="Администратор"))
        db.commit()
        if password == DEFAULT_ADMIN_PASSWORD:
            print(f"создан администратор «{login}» с паролем по умолчанию; "
                  "в промышленном контуре задайте NADZOR_ADMIN_PASSWORD")
    finally:
        db.close()
