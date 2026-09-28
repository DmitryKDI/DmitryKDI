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
    for name in (external_sync.URL_ENV, external_sync.TOKEN_ENV, "NADZOR_ALLOWED_HOSTS"):
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
    assert "500" in result.detail
    print("OK: ответ с ошибкой — SEND_FAILED с кодом, а не SENT")


def test_connection_failure_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setenv(external_sync.URL_ENV, "http://rin-gateway:8080/inbox")

    def broken(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(external_sync.httpx, "post", broken)
    result = external_sync.send(PACKAGE, idempotency_key="k")
    assert result.status == external_sync.SEND_FAILED
    assert "connection refused" in result.detail
    print("OK: обрыв связи — SEND_FAILED с причиной")
