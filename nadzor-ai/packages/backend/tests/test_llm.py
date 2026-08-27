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
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_local_provider_posts_to_ollamas_native_chat_endpoint():
    """local использует родной /api/chat Ollama, не /v1/chat/completions:
    реальный случай на боевой машине — тот совместимый эндпоинт молча
    отбрасывает num_ctx (его нет в структуре ChatCompletionRequest в
    исходниках Ollama), и любое сравнение с двумя картинками листа падало
    400-й ошибкой нехватки контекста. /api/chat это поле поддерживает
    всегда, независимо от версии Ollama."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"message": {"content": '{"significant": [], "checked_total": 1}'}})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="local", model="qwen3:8b", base_url="http://localhost:11434/v1")
        result = call_llm_json(config, "system prompt", "user text")

    assert result == {"significant": [], "checked_total": 1}
    # /v1 обрезается — это родной эндпоинт Ollama, не OpenAI-совместимый
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["format"] == "json"
    assert captured["json"]["model"] == "qwen3:8b"
    print("OK: local provider posts to Ollama's own /api/chat, stripping the /v1 suffix from base_url")


def test_vision_request_embeds_bare_base64_images_for_local_provider():
    """Родной формат Ollama — просто base64 в message['images'], без
    data:-префикса и без списка content-блоков в стиле OpenAI."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"message": {"content": "{}"}})

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    data_url = png_bytes_to_data_url(png)

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="local", model="qwen3:8b", base_url="http://localhost:11434/v1")
        call_llm_json(config, "system", "compare these", images=[data_url, data_url])

    message = captured["json"]["messages"][1]
    assert message["content"] == "compare these"
    assert len(message["images"]) == 2, message
    assert not message["images"][0].startswith("data:"), "родной формат Ollama — голый base64, без data:-префикса"
    print("OK: vision call embeds 2 bare-base64 images on the message for local provider")


def test_local_provider_requests_a_wider_context_than_ollamas_default():
    """Реальный случай: два листа-картинки с промптом легко превышают
    дефолтные 4096 токенов контекста Ollama, запрос падал 400-й ошибкой.
    options.num_ctx — единственное поле, которое родной /api/chat реально
    учитывает при выборе размера контекста."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"message": {"content": "{}"}})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="local", model="qwen2.5vl:7b", base_url="http://localhost:11434/v1")
        call_llm_json(config, "system", "user")

    assert captured["json"]["options"]["num_ctx"] > 4096, captured["json"]
    print("OK: local (Ollama) requests a context window bigger than Ollama's own 4096 default")


def test_openai_provider_does_not_send_ollama_specific_num_ctx():
    """num_ctx — расширение Ollama, не часть OpenAI API: настоящий OpenAI
    вернул бы ошибку на неизвестном поле, поэтому его нельзя слать всем."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="openai", api_key="sk-test", model="gpt-4o-mini")
        call_llm_json(config, "system", "user")

    assert "num_ctx" not in captured["json"], captured["json"]
    print("OK: real OpenAI requests stay free of the Ollama-only num_ctx field")


def test_yandexgpt_reads_credentials_from_env_not_from_config(monkeypatch):
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_GPT_FOLDER_ID", "b1gfolder123")
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"result": {"alternatives": [{"message": {"text": '{"significant": []}'}}]}})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        # api_key в LlmConfig намеренно пустой — этот провайдер берёт ключ и
        # Folder ID из .env (тех же переменных, что и у packages/llm_core),
        # чтобы не вводить секрет второй раз в этой отдельной панели настроек.
        config = LlmConfig(provider="yandexgpt", model="yandexgpt/latest")
        result = call_llm_json(config, "система", "сравни")

    assert result == {"significant": []}
    assert captured["url"] == "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    assert captured["headers"]["Authorization"] == "Api-Key test-key"
    assert captured["json"]["modelUri"] == "gpt://b1gfolder123/yandexgpt/latest"
    print("OK: YandexGPT берёт ключ/Folder ID из окружения, не из формы настроек")


def test_yandexgpt_missing_credentials_raise_clear_error(monkeypatch):
    monkeypatch.delenv("YANDEX_GPT_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_GPT_FOLDER_ID", raising=False)
    config = LlmConfig(provider="yandexgpt", model="yandexgpt/latest")
    try:
        call_llm_json(config, "система", "сравни")
        raise AssertionError("должно было упасть без реквизитов")
    except ValueError as e:
        assert "YANDEX_GPT" in str(e)
    print("OK: без ключа/Folder ID в .env — понятная ошибка")


def test_yandexgpt_rejects_images_with_clear_error(monkeypatch):
    monkeypatch.setenv("YANDEX_GPT_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_GPT_FOLDER_ID", "b1gfolder123")
    config = LlmConfig(provider="yandexgpt", model="yandexgpt/latest")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    try:
        call_llm_json(config, "система", "сравни", images=[png_bytes_to_data_url(png)])
        raise AssertionError("должно было упасть на картинке — YandexGPT здесь текстовый")
    except ValueError as e:
        assert "текст" in str(e)
    print("OK: попытка отправить картинку в YandexGPT — понятная ошибка, не сетевой сбой")


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

    with patch("app.llm.httpx.post", side_effect=_gigachat_fake_post(calls)):
        config = LlmConfig(provider="gigachat", api_key="base64-creds", model="GigaChat-2")
        result = call_llm_json(config, "система", "сравни", images=[png_bytes_to_data_url(png)])

    assert result == {"significant": []}
    oauth_call, upload_call, chat_call = calls
    assert oauth_call["headers"]["Authorization"] == "Basic base64-creds"
    assert oauth_call["data"]["scope"] == "GIGACHAT_API_PERS"
    assert upload_call["url"].endswith("/files")
    assert chat_call["json"]["messages"][1]["attachments"] == ["file-1"]
    assert "attachments" not in chat_call["json"]["messages"][0]
    print("OK: GigaChat получает токен, грузит файл и ссылается на него через attachments")


def test_gigachat_caches_token_across_two_calls(monkeypatch):
    llm_module._gigachat_token_cache.clear()
    calls: list[dict] = []

    with patch("app.llm.httpx.post", side_effect=_gigachat_fake_post(calls)):
        config = LlmConfig(provider="gigachat", api_key="base64-creds", model="GigaChat-2")
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
    test_local_provider_posts_to_ollamas_native_chat_endpoint()
    test_vision_request_embeds_bare_base64_images_for_local_provider()
    test_local_provider_requests_a_wider_context_than_ollamas_default()
    test_openai_provider_does_not_send_ollama_specific_num_ctx()
    test_gigachat_gets_token_uploads_image_then_calls_chat_with_attachment(None)
    test_gigachat_caches_token_across_two_calls(None)
    test_gigachat_missing_api_key_raises_clear_error()
    print("ALL PASS (запустите pytest для тестов с monkeypatch)")
