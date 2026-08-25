import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def test_local_provider_request_shape_and_no_auth_header():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"choices": [{"message": {"content": '{"significant": [], "checked_total": 1}'}}]})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="local", model="qwen3:8b", base_url="http://localhost:11434/v1")
        result = call_llm_json(config, "system prompt", "user text")

    assert result == {"significant": [], "checked_total": 1}
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in captured["headers"]
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["json"]["model"] == "qwen3:8b"
    print("OK: local provider posts to Ollama-compatible endpoint without auth header")


def test_vision_request_includes_images_for_local_provider():
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _FakeResponse({"choices": [{"message": {"content": "{}"}}]})

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    data_url = png_bytes_to_data_url(png)

    with patch("app.llm.httpx.post", side_effect=fake_post):
        config = LlmConfig(provider="local", model="qwen3:8b", base_url="http://localhost:11434/v1")
        call_llm_json(config, "system", "compare these", images=[data_url, data_url])

    content = captured["json"]["messages"][1]["content"]
    assert isinstance(content, list)
    image_blocks = [c for c in content if c.get("type") == "image_url"]
    assert len(image_blocks) == 2, content
    print("OK: vision call embeds 2 images as image_url blocks for local provider")


if __name__ == "__main__":
    test_extract_json_object_strips_think_block()
    test_extract_json_object_fenced()
    test_extract_json_object_invalid_returns_none()
    test_local_provider_request_shape_and_no_auth_header()
    test_vision_request_includes_images_for_local_provider()
    print("ALL PASS")
