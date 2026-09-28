"""Передача результатов во внешнюю систему (ИАИС «РиН», ТЗ 9.6).

Передача включается одной переменной окружения — адресом приёма. Пока адрес
не задан, пакет только формируется, и статус честно `LOCAL_ONLY`: выдать
несостоявшуюся передачу за отправку значило бы скрыть, что её не было.

Адрес проходит ту же проверку контура, что и адрес модели (`llm.is_local_url`):
внутренние имена сервисов и частные сети разрешены, внутреннее доменное имя —
только явно через `NADZOR_ALLOWED_HOSTS`. Выйти наружу опечаткой в окружении
нельзя.

Аутентификация — клиентский сертификат (ТЗ 9.6): пути к сертификату, ключу и
корневому сертификату приёмника задаются окружением. TLS с ГОСТ-алгоритмами
(УКЭП) выполняется сертифицированным СКЗИ заказчика — шлюзом перед приёмником;
этот модуль передаёт тот же сертификат стандартным TLS.

При недоступности приёмника (5xx, таймаут, обрыв связи) протокол остаётся
финализированным, статус синхронизации — `PENDING_SYNC`, и отправка
повторяется через 1, 5 и 15 минут (ТЗ 9.6). Каждая попытка — запись журнала
протокола. Сбой передачи не отменяет и не меняет решение инспектора.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

import httpx

from .llm import is_local_url

URL_ENV = "NADZOR_RIN_URL"
TOKEN_ENV = "NADZOR_RIN_TOKEN"  # noqa: S105 — имя переменной окружения, не секрет
CERT_ENV = "NADZOR_RIN_CLIENT_CERT"
KEY_ENV = "NADZOR_RIN_CLIENT_KEY"
CA_ENV = "NADZOR_RIN_CA"

# Сколько ждать ответа приёмника, секунд. Пакет — это протокол одного
# процесса, а не файлы документов, поэтому долгого приёма не ожидается.
SEND_TIMEOUT_S = 30
# Задержки повторов, минуты (ТЗ 9.6: до трёх повторов — 1, 5, 15 минут).
RETRY_DELAYS_MIN = (1, 5, 15)

LOCAL_ONLY = "LOCAL_ONLY"
SENT = "SENT"
PENDING_SYNC = "PENDING_SYNC"
SEND_FAILED = "SEND_FAILED"
SEND_REFUSED = "SEND_REFUSED"


@dataclass(frozen=True)
class SyncResult:
    status: str
    detail: str
    # Ошибка временная (5xx, таймаут, обрыв): отправку стоит повторить.
    retryable: bool = False


def _url() -> str:
    return os.environ.get(URL_ENV, "").strip()


def enabled() -> bool:
    return bool(_url())


def _tls() -> dict:
    """Клиентский сертификат и доверенный корень приёмника из окружения."""
    cert, key = os.environ.get(CERT_ENV, "").strip(), os.environ.get(KEY_ENV, "").strip()
    ca = os.environ.get(CA_ENV, "").strip()
    options: dict = {}
    if cert:
        options["cert"] = (cert, key) if key else cert
    if ca:
        options["verify"] = ca
    return options


def send(package: dict, *, idempotency_key: str) -> SyncResult:
    """Одна попытка отправки пакета, если передача включена.

    `idempotency_key` уходит заголовком `Idempotency-Key`: повторная отправка
    той же версии протокола опознаётся приёмником как повтор, а не как новый
    протокол.
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
        response = httpx.post(url, json=package, headers=headers, timeout=SEND_TIMEOUT_S,
                              **_tls())
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return SyncResult(SEND_FAILED, f"приёмник недоступен: {exc}", retryable=True)
    except (httpx.HTTPError, OSError) as exc:
        # Ошибка настройки (нет файла сертификата и т.п.) повтором не лечится.
        return SyncResult(SEND_FAILED, f"отправка не выполнена: {exc}")
    if response.is_success:
        return SyncResult(SENT, f"принято приёмником: HTTP {response.status_code}")
    return SyncResult(SEND_FAILED, f"приёмник отказал: HTTP {response.status_code} "
                                   f"{response.text[:200]}",
                      retryable=response.status_code >= 500)


def next_attempt(attempts_done: int, now: dt.datetime) -> dt.datetime | None:
    """Когда повторять после `attempts_done` неудачных попыток; None — повторы исчерпаны.

    Первая попытка — не повтор, поэтому после неё ждём RETRY_DELAYS_MIN[0].
    """
    retries_done = attempts_done - 1
    if retries_done >= len(RETRY_DELAYS_MIN):
        return None
    return now + dt.timedelta(minutes=RETRY_DELAYS_MIN[retries_done])


def attempt(db, run, *, now: dt.datetime | None = None) -> SyncResult:
    """Попытка отправки сохранённого пакета процесса с записью в журнал.

    Статус и расписание повторов пишутся в сам процесс, поэтому переживают
    перезапуск сервиса: фоновая задача продолжит с того же места.
    """
    from . import models

    now = now or dt.datetime.utcnow()
    result = send(run.sync_package or {}, idempotency_key=run.sync_key or f"nadzor-{run.id}")
    run.sync_attempts = (run.sync_attempts or 0) + 1
    status, detail = result.status, result.detail
    run.sync_next_at = None
    if result.retryable:
        when = next_attempt(run.sync_attempts, now)
        if when is not None:
            status = PENDING_SYNC
            run.sync_next_at = when
            detail += f"; повтор в {when:%H:%M} UTC"
        else:
            detail += f"; повторы исчерпаны ({len(RETRY_DELAYS_MIN)})"
    run.sync_status = status
    db.add(models.ProtocolEvent(run_id=run.id, action="EXPORT_EXTERNAL",
                                reason=f"попытка {run.sync_attempts}: {status}: {detail}"))
    db.commit()
    return SyncResult(status, detail, result.retryable)


def retry_due(now: dt.datetime | None = None) -> int:
    """Повторить отправку всех протоколов, которым подошёл срок. Возвращает число попыток."""
    from . import models
    from .db import SessionLocal

    now = now or dt.datetime.utcnow()
    done = 0
    with SessionLocal() as db:
        due = (db.query(models.OfficialRun)
               .filter(models.OfficialRun.sync_status == PENDING_SYNC,
                       models.OfficialRun.sync_next_at <= now).all())
        for run in due:
            attempt(db, run, now=now)
            done += 1
    return done
