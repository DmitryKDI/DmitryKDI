"""YandexGPTProvider: форма запроса к реальному API и честный отказ без ключа.

Стенда с боевыми реквизитами нет (см. providers/remote.py — «адаптеры не
проверялись на реальных стендах»), поэтому здесь проверяется контракт
запроса (URL, заголовок, тело) и обработка отсутствующих реквизитов, а не
сам вызов Yandex Cloud.
"""
from __future__ import annotations

import asyncio

import pytest
from llm_core.ports import CompletionRequest
from llm_core.providers.remote import YandexGPTProvider

CFG = {
    "model": "yandexgpt/latest",
    "base_url": "https://llm.api.cloud.yandex.net/foundationModels/v1",
    "api_key_env": "YANDEX_GPT_API_KEY",
    "folder_id_env": "YANDEX_GPT_FOLDER_ID",
}


def _request() -> CompletionRequest:
    return CompletionRequest(task="semantic_diff", system="Сравни листы.",
                             facts=[{"field": "class", "value": "B25"}],
                             untrusted_blocks=["содержимое документа"])


def test_missing_credentials_raise_clear_error(monkeypatch):
    monkeypatch.delenv("YANDEX_GPT_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_GPT_FOLDER_ID", raising=False)
    provider = YandexGPTProvider(CFG)
    with pytest.raises(RuntimeError, match="реквизиты"):
        asyncio.run(provider.complete(_request()))
    print("OK: без ключа/folder_id — понятная ошибка, а не сетевой сбой")


def test_request_shape_matches_yandex_api_contract(monkeypatch):
    """modelUri, Api-Key заголовок и messages — по документации Yandex Cloud
    Foundation Models. Ошибка здесь означает, что реальный вызов вернул бы 400/401."""
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_GPT_FOLDER_ID", "b1gfolder123")
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"alternatives": [{"message": {"text": '{"significant": []}'}}],
                               "usage": {"inputTextTokens": "42", "completionTokens": "7"}}}

    class _FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    import llm_core.providers.remote as remote_module
    monkeypatch.setattr(remote_module.httpx, "AsyncClient", _FakeClient)

    provider = YandexGPTProvider(CFG)
    result = asyncio.run(provider.complete(_request()))

    assert captured["url"] == "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    assert captured["headers"]["Authorization"] == "Api-Key test-key"
    body = captured["json"]
    assert body["modelUri"] == "gpt://b1gfolder123/yandexgpt/latest"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert "содержимое документа" in body["messages"][1]["text"]
    assert result.provider == "yandexgpt"
    assert result.tokens_in == 42 and result.tokens_out == 7
    print("OK: запрос собран по контракту Yandex Cloud Foundation Models")
