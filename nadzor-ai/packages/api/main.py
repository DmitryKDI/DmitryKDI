"""Точка входа HTTP-приложения.

Транспорт только по TLS (терминируется на периметре), список разрешённых
источников задаётся явно, заголовки безопасности выставляются на каждом ответе.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.audit_store import restore_chain
from api.db import create_schema, session_factory
from api.routers import admin, analysis, auth, objects
from api.seed import seed

CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                               "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_schema()
    async with session_factory()() as session:
        await restore_chain(session)      # продолжить цепочку аудита, а не начать заново
        await seed(session)
    yield


app = FastAPI(
    title="НАДЗОР.ИИ — модуль предиктивного строительного надзора",
    description="Система формирует гипотезы нарушений, а не заключения. "
                "Юридически значимые действия совершает только инспектор.",
    version="0.1.0", lifespan=lifespan,
)

app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Пользователю показывается, что произошло и что делать, без технических кодов."""
    return JSONResponse(status_code=422,
                        content={"detail": "Не удалось обработать запрос. Проверьте введённые "
                                           "данные и повторите попытку."})


@app.get("/api/health", tags=["Служебные"], summary="Проверка работоспособности")
async def health() -> dict:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(objects.router)
app.include_router(analysis.router)
app.include_router(admin.router)
