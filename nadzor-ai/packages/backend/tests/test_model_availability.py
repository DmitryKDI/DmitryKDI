"""Модель не меняется вслепую: перечень доступных аккаунту проверяется.

Техническое задание: «Модель нельзя менять без проверки списка моделей,
доступных аккаунту». Проверить было нечем — имя модели задано умолчанием, и
её недоступность выяснялась падением первого рабочего вызова, уже после
того как инспектор запустил разбор тома и оплатил очередь.

Три состояния различаются явно, как и везде в проекте: перечень получен и
модель в нём есть; перечень получен и модели в нём нет; перечень получить
не удалось. Последнее НЕ является ответом «модели нет»: сбой связи и
недоступную модель инспектор чинит по-разному (Г.10).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402

CONFIG = llm.LlmConfig(provider="gigachat", api_key="dGVzdC1pZDp0ZXN0LXNlY3JldA==")


def _answer(monkeypatch, payload=None, error: Exception | None = None):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return payload or {}

    def fake_get(url, **kwargs):
        captured.update({"url": url, **kwargs})
        if error is not None:
            raise error
        return Response()

    monkeypatch.setattr(llm, "_gigachat_token", lambda *a, **kw: "token")
    monkeypatch.setattr(llm.httpx, "get", fake_get)
    return captured


def test_available_models_are_listed(monkeypatch):
    captured = _answer(monkeypatch, {"data": [{"id": "ModelA"}, {"id": "ModelB"}]})

    result = llm.available_models(CONFIG)

    assert result.models == ["ModelA", "ModelB"]
    assert not result.error
    assert captured["url"].endswith("/v1/models")
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_configured_model_is_checked_against_the_list(monkeypatch):
    _answer(monkeypatch, {"data": [{"id": "ModelA"}]})

    result = llm.available_models(llm.LlmConfig(
        provider="gigachat", api_key=CONFIG.api_key, model="ModelB"))

    assert result.models == ["ModelA"]
    assert result.configured_available is False


def test_unreachable_list_is_not_read_as_missing_model(monkeypatch):
    """Сбой связи не означает, что модели у аккаунта нет."""
    _answer(monkeypatch, error=RuntimeError("нет связи"))

    result = llm.available_models(CONFIG)

    assert result.models == []
    assert result.configured_available is None, "неизвестно — это не «нет»"
    assert "нет связи" in result.error


def test_missing_key_is_named_not_guessed():
    result = llm.available_models(llm.LlmConfig(provider="gigachat", api_key=""))

    assert result.configured_available is None
    assert "ключ" in result.error.casefold()


def test_other_providers_say_so_instead_of_pretending():
    result = llm.available_models(llm.LlmConfig(provider="anthropic", api_key="x"))

    assert result.configured_available is None
    assert result.error


def test_llm_check_reports_the_model_and_its_availability(monkeypatch):
    """Инспектор видит выбранную модель и то, есть ли она у аккаунта."""
    from app import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "check_llm_reachable", lambda config: (True, "связь есть"))
    monkeypatch.setattr(
        main, "available_models",
        lambda config: llm.ModelAvailability(["ModelA"], "ModelB", False))
    monkeypatch.setattr(main, "_llm_config",
                        lambda db: llm.LlmConfig(provider="gigachat", api_key="x",
                                                 model="ModelB"))

    body = TestClient(main.app).get("/llm-check").json()

    assert body["model"] == "ModelB"
    assert body["model_available"] is False
    assert body["models_available"] == ["ModelA"]


def test_llm_check_does_not_claim_the_model_is_missing_when_offline(monkeypatch):
    """Нет связи — нет и суждения о модели, а не «модели нет»."""
    from app import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main, "check_llm_reachable", lambda config: (False, "нет связи"))
    monkeypatch.setattr(main, "_llm_config",
                        lambda db: llm.LlmConfig(provider="gigachat", api_key="x"))

    body = TestClient(main.app).get("/llm-check").json()

    assert body["model_available"] is None
    assert "связи нет" in body["models_message"]
