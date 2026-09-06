"""GigaChatProvider v2 (Client ID + Secret): форма запроса и честный отказ без ключа.

Стенда с боевыми реквизитами нет, поэтому проверяется контракт запроса
(URL, заголовок, тело, vision-формат) и обработка отсутствующих реквизитов.
"""
from __future__ import annotations

import asyncio
import base64
import time

import pytest
from llm_core.ports import CompletionRequest, ImageBlock
from llm_core.providers.remote import GigaChatProvider

CFG = {
    "model": "GigaChat-Pro",
    "api_url": "https://api.giga.chat",
    "oauth_url": "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
    "client_id_env": "GIGACHAT_CLIENT_ID",
    "client_secret_env": "GIGACHAT_CLIENT_SECRET",
    "scope": "GIGACHAT_API_PERS",
}


def _request() -> CompletionRequest:
    return CompletionRequest(
        task="semantic_diff",
        system="Сравни листы.",
        facts=[{"field": "class", "value": "B25"}],
        untrusted_blocks=["содержимое документа"],
    )


def _request_with_images() -> CompletionRequest:
    png_data = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    return CompletionRequest(
        task="semantic_diff",
        system="Сравни листы.",
        facts=[{"field": "class", "value": "B25"}],
        untrusted_blocks=["содержимое документа"],
        images=[ImageBlock(media_type="image/png", data_b64=png_data, label="лист 1")],
    )


def test_missing_client_credentials_raise_clear_error(monkeypatch):
    """Без Client ID/Secret — понятная ошибка, а не сетевой сбой."""
    monkeypatch.delenv("GIGACHAT_CLIENT_ID", raising=False)
    monkeypatch.delenv("GIGACHAT_CLIENT_SECRET", raising=False)
    provider = GigaChatProvider(CFG)
    with pytest.raises(RuntimeError, match="Client ID/Secret"):
        asyncio.run(provider.complete(_request()))
    print("OK: без Client ID/Secret — понятная ошибка")


def test_request_shape_matches_gigachat_api_contract(monkeypatch):
    """Проверяем OAuth-токен, Bearer-заголовок, JSON-формат ответа."""
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", "test-client-secret")

    call_log = []

    class _TokenResp:
        status_code = 200

        def json(self):
            return {"access_token": "tok123", "expires_at": int(time.time() * 1000) + 1500000}

    class _ChatResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }

    class _FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, data=None, json=None):
            call_log.append({"url": url, "headers": headers, "data": data, "json": json})
            if "oauth" in url:
                return _TokenResp()
            return _ChatResp()

    import llm_core.providers.remote as remote_module
    monkeypatch.setattr(remote_module.httpx, "AsyncClient", _FakeClient)

    provider = GigaChatProvider(CFG)
    result = asyncio.run(provider.complete(_request()))

    # OAuth вызван с правильными credentials
    oauth_call = call_log[0]
    assert "oauth" in oauth_call["url"]
    assert oauth_call["data"]["scope"] == "GIGACHAT_API_PERS"
    assert "Basic " in oauth_call["headers"]["Authorization"]

    # Chat completions вызван с Bearer-токеном на новом URL
    chat_call = call_log[1]
    assert "chat/completions" in chat_call["url"]
    assert "api.giga.chat" in chat_call["url"]
    assert chat_call["headers"]["Authorization"] == "Bearer tok123"
    body = chat_call["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert result.provider == "gigachat"
    assert result.tokens_in == 100 and result.tokens_out == 20
    print("OK: запрос к GigaChat собран по контракту v2")


def test_vision_content_format(monkeypatch):
    """Vision-запрос: картинка не инлайн — сначала POST /v1/files (мультипарт),
    затем ссылка на полученный id в attachments сообщения (не в content).
    Тот же контракт, что packages/backend/app/llm.py (см. test_llm.py:
    test_gigachat_gets_token_uploads_image_then_calls_chat_with_attachment)."""
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", "test-client-secret")

    call_log = []

    class _TokenResp:
        status_code = 200

        def json(self):
            return {"access_token": "tok456", "expires_at": int(time.time() * 1000) + 1500000}

    class _UploadResp:
        status_code = 200

        def json(self):
            return {"id": "file-1"}

    class _ChatResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": '{"findings": []}'}}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 30},
            }

    class _FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, data=None, json=None, files=None):
            call_log.append({"url": url, "json": json, "files": files, "data": data})
            if "oauth" in url:
                return _TokenResp()
            if url.endswith("/files"):
                return _UploadResp()
            return _ChatResp()

    import llm_core.providers.remote as remote_module
    monkeypatch.setattr(remote_module.httpx, "AsyncClient", _FakeClient)

    provider = GigaChatProvider(CFG)
    result = asyncio.run(provider.complete(_request_with_images()))

    upload_call = call_log[1]
    assert upload_call["url"].endswith("/files")
    assert upload_call["files"] is not None, "Картинка должна идти файлом (multipart), не JSON-полем"

    chat_call = call_log[2]
    body = chat_call["json"]
    assert body["messages"][1]["attachments"] == ["file-1"]
    assert "attachments" not in body["messages"][0]
    assert isinstance(body["messages"][1]["content"], str), "content должен остаться обычным текстом, картинка — только в attachments"
    assert result.provider == "gigachat"
    print("OK: vision-контент собран через загрузку файла и attachments")


def test_token_caching_works(monkeypatch):
    """Токен кэшируется и не запрашивается повторно в течение TTL."""
    monkeypatch.setenv("GIGACHAT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GIGACHAT_CLIENT_SECRET", "test-client-secret")

    oauth_calls = 0

    class _TokenResp:
        status_code = 200

        def json(self):
            return {"access_token": "tok_cached", "expires_at": int(time.time() * 1000) + 1500000}

    class _ChatResp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": '{"findings": []}'}}]}

    class _FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, data=None, json=None):
            nonlocal oauth_calls
            if "oauth" in url:
                oauth_calls += 1
                return _TokenResp()
            return _ChatResp()

    import llm_core.providers.remote as remote_module
    monkeypatch.setattr(remote_module.httpx, "AsyncClient", _FakeClient)

    provider = GigaChatProvider(CFG)
    # Первый вызов — OAuth + Chat
    asyncio.run(provider.complete(_request()))
    assert oauth_calls == 1

    # Второй вызов — только Chat (токен из кэша)
    asyncio.run(provider.complete(_request()))
    assert oauth_calls == 1, "Токен должен быть взят из кэша, без повторного OAuth"
    print("OK: кэширование токена работает")
