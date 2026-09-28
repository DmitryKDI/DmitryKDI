"""Вход, выход, управление пользователями и журнал аудита (ТЗ 12)."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import audit, auth, models
from .db import get_session

router = APIRouter(prefix="/api/v1", tags=["auth"])


class LoginInput(BaseModel):
    login: str
    password: str


class UserInput(BaseModel):
    login: str
    password: str
    role: str
    full_name: str = ""


class UserUpdate(BaseModel):
    role: str | None = None
    full_name: str | None = None
    is_active: bool | None = None
    password: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


def _user_dict(user: models.User) -> dict:
    return {"id": user.id, "login": user.login, "role": user.role,
            "role_title": auth.ROLE_TITLES.get(user.role, user.role),
            "full_name": user.full_name, "is_active": user.is_active,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None}


def _cookie_secure() -> bool:
    return os.environ.get("NADZOR_COOKIE_SECURE", "0").strip() in {"1", "true", "yes"}


@router.post("/auth/login", summary="Вход по логину и паролю")
def login(body: LoginInput, request: Request, response: Response,
          db: Session = Depends(get_session)):
    audit.note(request, login=body.login.strip())
    user = db.query(models.User).filter_by(login=body.login.strip()).first()
    if user is None or not user.is_active or not auth.verify_password(body.password,
                                                                       user.password_hash):
        raise HTTPException(401, "неверный логин или пароль")
    token = auth.create_session(db, user)
    request.state.principal = auth.Principal(user.id, user.login, user.role, user.full_name)
    response.set_cookie(auth.COOKIE_NAME, token, httponly=True, samesite="strict",
                        secure=_cookie_secure(), max_age=auth.SESSION_TTL_HOURS * 3600,
                        path="/")
    return {"token": token, "token_type": "bearer", "user": _user_dict(user)}


@router.post("/auth/logout", summary="Выход")
def logout(request: Request, response: Response, db: Session = Depends(get_session)):
    token = auth.request_token(request)
    if token:
        auth.drop_session(db, token)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me", summary="Текущий пользователь")
def me(user: auth.Principal = Depends(auth.current_user), db: Session = Depends(get_session)):
    return _user_dict(db.get(models.User, user.user_id))


@router.get("/auth/session", summary="Состояние сессии (без ошибки, если вход не выполнен)")
def session(request: Request, db: Session = Depends(get_session)):
    """Интерфейсу при открытии нужно узнать, выполнен ли вход. 401 здесь
    выглядел бы в консоли браузера как ошибка, хотя это обычное состояние."""
    try:
        user = auth.current_user(request, db)
    except HTTPException:
        return {"user": None}
    return {"user": _user_dict(db.get(models.User, user.user_id))}


@router.post("/auth/password", summary="Сменить свой пароль")
def change_password(body: PasswordChange, user: auth.Principal = Depends(auth.current_user),
                    db: Session = Depends(get_session)):
    row = db.get(models.User, user.user_id)
    if not auth.verify_password(body.current_password, row.password_hash):
        raise HTTPException(403, "текущий пароль неверен")
    auth.validate_password(body.new_password)
    row.password_hash = auth.hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.get("/admin/users", summary="Пользователи")
def users(_: auth.Principal = Depends(auth.require("admin")),
          db: Session = Depends(get_session)):
    return [_user_dict(row) for row in db.query(models.User).order_by(models.User.id).all()]


@router.post("/admin/users", summary="Создать пользователя")
def create_user(body: UserInput, _: auth.Principal = Depends(auth.require("admin")),
                db: Session = Depends(get_session)):
    if body.role not in auth.ROLES:
        raise HTTPException(422, "роль: " + ", ".join(auth.ROLES))
    login_name = body.login.strip()
    if not login_name:
        raise HTTPException(422, "укажите логин")
    if db.query(models.User).filter_by(login=login_name).first():
        raise HTTPException(409, "такой логин уже есть")
    auth.validate_password(body.password)
    row = models.User(login=login_name, password_hash=auth.hash_password(body.password),
                      role=body.role, full_name=body.full_name.strip())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _user_dict(row)


@router.patch("/admin/users/{user_id}", summary="Изменить пользователя")
def update_user(user_id: int, body: UserUpdate,
                admin: auth.Principal = Depends(auth.require("admin")),
                db: Session = Depends(get_session)):
    row = db.get(models.User, user_id)
    if row is None:
        raise HTTPException(404, "пользователь не найден")
    if body.role is not None:
        if body.role not in auth.ROLES:
            raise HTTPException(422, "роль: " + ", ".join(auth.ROLES))
        row.role = body.role
    if body.full_name is not None:
        row.full_name = body.full_name.strip()
    if body.is_active is not None:
        if row.id == admin.user_id and not body.is_active:
            raise HTTPException(409, "нельзя отключить собственную учётную запись")
        row.is_active = body.is_active
        if not body.is_active:
            db.query(models.AuthSession).filter_by(user_id=row.id).delete()
    if body.password:
        auth.validate_password(body.password)
        row.password_hash = auth.hash_password(body.password)
        db.query(models.AuthSession).filter_by(user_id=row.id).delete()
    db.commit()
    db.refresh(row)
    return _user_dict(row)


@router.get("/admin/audit", summary="Журнал аудита")
def audit_log(limit: int = Query(200, ge=1, le=5000), user: str | None = None,
              action: str | None = None,
              _: auth.Principal = Depends(auth.require("admin", "ml_engineer")),
              db: Session = Depends(get_session)):
    query = db.query(models.AuditLog)
    if user:
        query = query.filter(models.AuditLog.login == user)
    if action:
        query = query.filter(models.AuditLog.action.contains(action))
    rows = query.order_by(models.AuditLog.id.desc()).limit(limit).all()
    return [{"id": row.id, "timestamp": row.timestamp.isoformat(), "user_id": row.user_id,
             "login": row.login, "action": row.action, "object_id": row.object_id,
             "status_code": row.status_code, "ip_address": row.ip_address,
             "user_agent": row.user_agent, "details": row.details} for row in rows]
