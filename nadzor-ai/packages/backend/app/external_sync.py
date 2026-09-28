"""Передача пакета результатов во внешнюю систему (ИАИС «РиН», ТЗ 9.3).

Передача включается одной переменной окружения — адресом приёма. Пока адрес
не задан, пакет только формируется, и статус честно `LOCAL_ONLY`: выдать
несостоявшуюся передачу за отправку значило бы скрыть, что её не было.

Адрес проходит ту же проверку контура, что и адрес модели (`llm.is_local_url`):
внутренние имена сервисов и частные сети разрешены, внутреннее доменное имя —
только явно через `NADZOR_ALLOWED_HOSTS`. Выйти наружу опечаткой в окружении
нельзя.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .llm import is_local_url

URL_ENV = "NADZOR_RIN_URL"
TOKEN_ENV = "NADZOR_RIN_TOKEN"  # noqa: S105 — имя переменной окружения, не секрет

# Сколько ждать ответа приёмника, секунд. Пакет — это протокол одного
# процесса, а не файлы документов, поэтому долгого приёма не ожидается.
SEND_TIMEOUT_S = 30

LOCAL_ONLY = "LOCAL_ONLY"
SENT = "SENT"
SEND_FAILED = "SEND_FAILED"
SEND_REFUSED = "SEND_REFUSED"


@dataclass(frozen=True)
class SyncResult:
    status: str
    detail: str


def _url() -> str:
    return os.environ.get(URL_ENV, "").strip()


def enabled() -> bool:
    return bool(_url())


def send(package: dict, *, idempotency_key: str) -> SyncResult:
    """Отправить пакет, если передача включена.

    `idempotency_key` уходит заголовком `Idempotency-Key`: повторная отправка
    той же версии протокола (например, после сбоя) опознаётся приёмником как
    повтор, а не как новый протокол.
    """
    url = _url()
    if not url:
        return SyncResult(LOCAL_ONLY, f"передача не включена: адрес приёма ({URL_ENV}) не задан")
    if not is_local_url(url):
        return SyncResult(SEND_REFUSED, "адрес приёма вне контура: разрешены имена сервисов, "
                                        "частные сети и имена из NADZOR_ALLOWED_HOSTS")
    headers = {"Idempotency-Key": idempotency_key}
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(url, json=package, headers=headers, timeout=SEND_TIMEOUT_S)
    except httpx.HTTPError as exc:
        return SyncResult(SEND_FAILED, f"приёмник недоступен: {exc}")
    if response.is_success:
        return SyncResult(SENT, f"принято приёмником: HTTP {response.status_code}")
    return SyncResult(SEND_FAILED, f"приёмник отказал: HTTP {response.status_code} "
                                   f"{response.text[:200]}")
