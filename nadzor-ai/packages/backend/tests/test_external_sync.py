"""Передача пакета во внешнюю систему включается одной переменной окружения.

Без адреса пакет только формируется (LOCAL_ONLY), с адресом — отправляется.
Отказ отправки никогда не выдаётся за отправленное, а адрес вне контура
отклоняется до сетевого вызова — так же, как у модели.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import external_sync  # noqa: E402

PACKAGE = {"process_id": 7, "versions": {"protocol_version": 2},
           "confirmed_violations": [{"finding_id": "M-001:matrix"}]}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (external_sync.URL_ENV, external_sync.TOKEN_ENV, external_sync.CERT_ENV,
                 external_sync.KEY_ENV, external_sync.CA_ENV, "NADZOR_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def _no_network(monkeypatch):
    monkeypatch.setattr(external_sync.httpx, "post", lambda *a, **kw: pytest.fail(
        "сетевой вызов не должен был состояться"))


def test_without_address_the_package_stays_local(monkeypatch):
    _no_network(monkeypatch)
    result = external_sync.send(PACKAGE, idempotency_key="nadzor-7-2")
    assert result.status == external_sync.LOCAL_ONLY
    assert not external_sync.enabled()
    print("OK: без адреса пакет формируется, но никуда не уходит")


def test_package_is_sent_to_the_configured_address(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")
    monkeypatch.setenv(external_sync.TOKEN_ENV, "secret-token")
    seen = {}

    def fake_post(url, json, headers, timeout):
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return httpx.Response(202, request=httpx.Request("POST", url))

    monkeypatch.setattr(external_sync.httpx, "post", fake_post)
    result = external_sync.send(PACKAGE, idempotency_key="nadzor-7-2")

    assert result.status == external_sync.SENT, result
    assert seen["url"] == "http://rin-gateway:8080/inbox"
    assert seen["json"] == PACKAGE
    assert seen["headers"]["Authorization"] == "Bearer secret-token"
    assert seen["headers"]["Idempotency-Key"] == "nadzor-7-2"
    print("OK: с адресом пакет отправляется, повтор опознаётся по ключу")


def test_address_outside_the_contour_is_refused_before_sending(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "https://rin.example.com/inbox")
    _no_network(monkeypatch)
    result = external_sync.send(PACKAGE, idempotency_key="nadzor-7-2")
    assert result.status == external_sync.SEND_REFUSED
    assert "контур" in result.detail
    print("OK: адрес вне контура отклонён до отправки")


def test_explicitly_allowed_internal_host_is_accepted(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "https://rin.internal.local/inbox")
    monkeypatch.setenv("NADZOR_ALLOWED_HOSTS", "rin.internal.local")
    monkeypatch.setattr(external_sync.httpx, "post", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("POST", url)))
    assert external_sync.send(PACKAGE, idempotency_key="k").status == external_sync.SENT
    print("OK: внутреннее имя с точкой разрешается явно через NADZOR_ALLOWED_HOSTS")


def test_rejection_by_the_receiver_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")
    monkeypatch.setattr(external_sync.httpx, "post", lambda url, **kw: httpx.Response(
        500, text="internal error", request=httpx.Request("POST", url)))
    result = external_sync.send(PACKAGE, idempotency_key="k")
    assert result.status == external_sync.SEND_FAILED
    assert "500" in result.detail and result.retryable
    print("OK: ответ с ошибкой — SEND_FAILED с кодом, а не SENT")


def test_connection_failure_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")

    def broken(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(external_sync.httpx, "post", broken)
    result = external_sync.send(PACKAGE, idempotency_key="k")
    assert result.status == external_sync.SEND_FAILED
    assert "connection refused" in result.detail and result.retryable
    print("OK: обрыв связи — SEND_FAILED с причиной")


def test_client_rejection_is_not_retried(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")
    monkeypatch.setattr(external_sync.httpx, "post", lambda url, **kw: httpx.Response(
        400, text="bad package", request=httpx.Request("POST", url)))
    result = external_sync.send(PACKAGE, idempotency_key="k")
    assert result.status == external_sync.SEND_FAILED and not result.retryable
    print("OK: отказ 4xx повтором не лечится и не повторяется")


def test_client_certificate_is_used_for_tls(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "https://10.0.0.5/inbox")
    monkeypatch.setenv(external_sync.CERT_ENV, "/certs/client.pem")
    monkeypatch.setenv(external_sync.KEY_ENV, "/certs/client.key")
    monkeypatch.setenv(external_sync.CA_ENV, "/certs/rin-ca.pem")
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(external_sync.httpx, "post", fake_post)
    assert external_sync.send(PACKAGE, idempotency_key="k").status == external_sync.SENT
    assert seen["cert"] == ("/certs/client.pem", "/certs/client.key")
    assert seen["verify"] == "/certs/rin-ca.pem"
    print("OK: передача аутентифицируется клиентским сертификатом (ТЗ 9.6)")


def test_retry_schedule_is_1_5_15_minutes():
    import datetime as dt

    now = dt.datetime(2026, 1, 1, 12, 0)
    delays = [external_sync.next_attempt(n, now) for n in (1, 2, 3, 4)]
    assert [(d - now).seconds // 60 for d in delays[:3]] == [1, 5, 15]
    assert delays[3] is None
    print("OK: повторы через 1, 5 и 15 минут, затем прекращаются")


def test_unavailable_receiver_gives_pending_sync_then_retries(monkeypatch):
    import datetime as dt

    from app import models
    from app.db import SessionLocal

    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")
    calls = []

    def flaky(url, **kwargs):
        calls.append(kwargs["headers"]["Idempotency-Key"])
        if len(calls) < 3:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(202, request=httpx.Request("POST", url))

    monkeypatch.setattr(external_sync.httpx, "post", flaky)
    now = dt.datetime(2026, 1, 1, 12, 0)
    with SessionLocal() as db:
        run = models.OfficialRun(object_id="sync-retry", status="completed",
                                 input_snapshot=[], sync_package=PACKAGE, sync_key="key-1",
                                 finalized_at=now, finalized_by="insp")
        db.add(run)
        db.commit()
        first = external_sync.attempt(db, run, now=now)
        run_id = run.id
    assert first.status == external_sync.PENDING_SYNC

    assert external_sync.retry_due(now) == 0, "повтор раньше срока"
    assert external_sync.retry_due(now + dt.timedelta(minutes=1)) == 1
    assert external_sync.retry_due(now + dt.timedelta(minutes=7)) == 1
    with SessionLocal() as db:
        run = db.get(models.OfficialRun, run_id)
        assert run.sync_status == external_sync.SENT and run.sync_attempts == 3
        assert run.finalized_at is not None, "сбой передачи не трогает финализацию"
        events = db.query(models.ProtocolEvent).filter_by(
            run_id=run_id, action="EXPORT_EXTERNAL").count()
    assert events == 3 and set(calls) == {"key-1"}
    print("OK: недоступность — PENDING_SYNC, повторы с журналом, финализация не меняется")
