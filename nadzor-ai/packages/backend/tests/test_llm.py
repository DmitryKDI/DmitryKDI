import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm as llm_module
from app.llm import LlmConfig, call_llm_json, extract_json_object, png_bytes_to_data_url


def test_extract_json_object_strips_think_block():
    text = "<think>рассуждаю о нормализации данных...</think>{\"significant\": [], \"checked_total\": 3}"
    result = extract_json_object(text)
    assert result == {"significant": [], "checked_total": 3}, result
    print("OK: <think> block stripped before JSON extraction")


def test_extract_json_object_fenced():
    text = "Вот результат:\n```json\n{\"a\": 1}\n```\nготово"
    result = extract_json_object(text)
    assert result == {"a": 1}, result
    print("OK: fenced JSON extracted")


def test_extract_json_object_invalid_returns_none():
    assert extract_json_object("просто текст без JSON") is None
    print("OK: no-JSON text returns None, not an exception")


class _FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_anthropic_provider_posts_messages_with_system_and_api_key_header():
    """Г.71 — единственный оставшийся не-GigaChat провайдер. Проверяем сам
    HTTP-контракт: system как отдельное поле (не в messages), x-api-key
    заголовок (не Bearer, как у большинства остальных API)."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"content": [{"text": '{"significant": [], "checked_total": 2}'}]})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
        result = call_llm_json(config, "system prompt", "user text")

    assert result == {"significant": [], "checked_total": 2}
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["json"]["system"] == "system prompt"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    print("OK: anthropic provider posts to /v1/messages with system field and x-api-key header")


def test_anthropic_vision_request_embeds_base64_image_blocks():
    """Формат Anthropic — content-блоки {"type": "image", "source": {...}},
    не bare-base64 (Ollama) и не image_url (OpenAI-style)."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"content": [{"text": "{}"}]})

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    data_url = png_bytes_to_data_url(png)

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="anthropic", api_key="sk-ant-test", model="claude-sonnet-5")
        call_llm_json(config, "system", "compare these", images=[data_url, data_url])

    content = captured["json"]["messages"][0]["content"]
    image_blocks = [c for c in content if c.get("type") == "image"]
    assert len(image_blocks) == 2, content
    assert image_blocks[0]["source"]["type"] == "base64"
    print("OK: anthropic vision call embeds 2 base64 image content-blocks")


def _gigachat_fake_post(calls):
    def fake_post(url, json=None, data=None, files=None, headers=None, timeout=None, verify=None):
        calls.append({"url": url, "json": json, "data": data, "files": files, "headers": headers})
        if url == llm_module.GIGACHAT_OAUTH_URL:
            return _FakeResponse({"access_token": "tok-1", "expires_at": (__import__("time").time() + 1800) * 1000})
        if url.endswith("/files"):
            return _FakeResponse({"id": "file-1"})
        if url.endswith("/chat/completions"):
            return _FakeResponse({"choices": [{"message": {"content": '{"significant": []}'}}]})
        raise AssertionError(f"unexpected url: {url}")
    return fake_post


def test_gigachat_gets_token_uploads_image_then_calls_chat_with_attachment(monkeypatch):
    """Картинка у GigaChat не инлайн, как у OpenAI/Anthropic/Google — сначала
    отдельная загрузка файла (POST /files), потом ссылка на его id в
    attachments сообщения (не в content)."""
    llm_module._gigachat_token_cache.clear()
    calls: list[dict] = []
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100

    api_key = "dGVzdC1pZDp0ZXN0LXNlY3JldA=="  # Base64("test-id:test-secret")
    with patch("app.llm.httpx.post", side_effect=_gigachat_fake_post(calls)):
        config = LlmConfig(provider="gigachat", api_key=api_key, model="GigaChat-2")
        result = call_llm_json(config, "система", "сравни", images=[png_bytes_to_data_url(png)])

    assert result == {"significant": []}
    oauth_call, upload_call, chat_call = calls
    assert oauth_call["headers"]["Authorization"] == f"Basic {api_key}"
    assert oauth_call["data"]["scope"] == "GIGACHAT_API_PERS"
    assert upload_call["url"].endswith("/files")
    assert chat_call["json"]["messages"][1]["attachments"] == ["file-1"]
    assert "attachments" not in chat_call["json"]["messages"][0]
    print("OK: GigaChat получает токен, грузит файл и ссылается на него через attachments")


def test_post_json_retries_on_429_then_succeeds(monkeypatch):
    """Г.78 — реальный найденный сбой: пачка последовательных вызовов
    (requirement_text_verify.py делает по вызову на каждую страницу РД для
    каждого оставшегося требования — сотни вызовов подряд на комплекте с
    полсотней требований) упирается в лимит частоты GigaChat раньше, чем в
    реальный сетевой сбой — 429 без ретрая выглядел неотличимо от
    "провайдер недоступен"."""
    monkeypatch.setattr(llm_module, "_RATE_LIMIT_BASE_DELAY", 0.0)  # не ждать реально в тесте
    llm_module._gigachat_token_cache.clear()
    attempts = {"oauth": 0}

    def fake_post(url, json=None, data=None, files=None, headers=None, timeout=None, verify=None):
        if url == llm_module.GIGACHAT_OAUTH_URL:
            attempts["oauth"] += 1
            if attempts["oauth"] < 3:
                return _FakeResponse({}, status_code=429, headers={"Retry-After": "0"})
            return _FakeResponse({"access_token": "tok-1", "expires_at": (__import__("time").time() + 1800) * 1000})
        if url.endswith("/chat/completions"):
            return _FakeResponse({"choices": [{"message": {"content": '{"significant": []}'}}]})
        raise AssertionError(f"unexpected url: {url}")

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="gigachat", api_key="dGVzdC1pZDp0ZXN0LXNlY3JldA==", model="GigaChat-2")
        result = call_llm_json(config, "система", "текст")

    assert result == {"significant": []}
    assert attempts["oauth"] == 3, "должен был повторить запрос токена после двух 429, не сдаться сразу"
    print("OK: 429 (rate limit) от провайдера повторяется с задержкой, а не считается немедленным сбоем")


def test_post_json_gives_up_after_max_retries_on_persistent_429(monkeypatch):
    monkeypatch.setattr(llm_module, "_RATE_LIMIT_BASE_DELAY", 0.0)
    llm_module._gigachat_token_cache.clear()

    def fake_post(url, json=None, data=None, files=None, headers=None, timeout=None, verify=None):
        return _FakeResponse({}, status_code=429, headers={})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="gigachat", api_key="dGVzdC1pZDp0ZXN0LXNlY3JldA==", model="GigaChat-2")
        try:
            call_llm_json(config, "система", "текст")
            raised = False
        except Exception:
            raised = True
    assert raised, "постоянный 429 обязан в итоге дать честную ошибку, не бесконечный ретрай и не тихий успех"
    print("OK: постоянный rate-limit в итоге даёт явную ошибку после ограниченного числа попыток")


def test_gigachat_caches_token_across_two_calls(monkeypatch):
    llm_module._gigachat_token_cache.clear()
    calls: list[dict] = []

    with patch("app.llm.httpx.post", side_effect=_gigachat_fake_post(calls)):
        config = LlmConfig(provider="gigachat", api_key="dGVzdC1pZDp0ZXN0LXNlY3JldA==", model="GigaChat-2")
        call_llm_json(config, "система", "раз")
        call_llm_json(config, "система", "два")

    oauth_calls = [c for c in calls if c["url"] == llm_module.GIGACHAT_OAUTH_URL]
    assert len(oauth_calls) == 1, "второй вызов должен был взять токен из кэша, не за новым"
    print("OK: токен GigaChat кэшируется между вызовами внутри одного прогона")


def test_gigachat_missing_api_key_raises_clear_error():
    config = LlmConfig(provider="gigachat", model="GigaChat-2")
    try:
        call_llm_json(config, "система", "сравни")
        raise AssertionError("должно было упасть без авторизационного ключа")
    except ValueError as e:
        assert "api_key" in str(e) or "ключ" in str(e)
    print("OK: без авторизационного ключа GigaChat — понятная ошибка, не сетевой сбой")


if __name__ == "__main__":
    test_extract_json_object_strips_think_block()
    test_extract_json_object_fenced()
    test_extract_json_object_invalid_returns_none()
    test_anthropic_provider_posts_messages_with_system_and_api_key_header()
    test_anthropic_vision_request_embeds_base64_image_blocks()
    test_gigachat_gets_token_uploads_image_then_calls_chat_with_attachment(None)
    test_gigachat_caches_token_across_two_calls(None)
    test_gigachat_missing_api_key_raises_clear_error()
    print("ALL PASS (запустите pytest для тестов с monkeypatch)")
