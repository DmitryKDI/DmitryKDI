"""Провайдер-абстракция для LLM: локальная модель (Ollama, OpenAI-совместимый
эндпоинт) по умолчанию — данные не покидают компьютер — плюс переключаемые
внешние API (OpenAI, Anthropic, Google) для случаев, когда важна скорость, а
не приватность.

Порт AI_PROVIDERS/buildLlmRequest/callLlm/extractJsonObject из
nadzor-browser/main.js: тот же контракт structured-JSON вывода
(response_format / responseMimeType), который в браузерном инструменте решил
проблему рассуждающей модели, уходящей в посторонний текст вместо ответа по
задаче — здесь применяется и к тексту, и к vision-запросам.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx

DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

PROVIDER_DEFAULT_MODELS = {
    # Сравнение листов идёт картинками (см. vision.compare_page_pair), поэтому
    # локальная модель по умолчанию обязана уметь зрение. Текстовая qwen3
    # молча возвращала бы отказ на каждый чертёж.
    "local": "qwen2.5vl:7b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "google": "gemini-2.5-flash",
}

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class LlmConfig:
    provider: str  # 'local' | 'openai' | 'anthropic' | 'google'
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def resolved_model(self) -> str:
        return self.model or PROVIDER_DEFAULT_MODELS.get(self.provider, "")

    def resolved_base_url(self) -> str:
        return self.base_url or (DEFAULT_LOCAL_BASE_URL if self.provider == "local" else "")


def extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    m = _FENCED_JSON_RE.search(cleaned)
    candidate = m.group(1) if m else cleaned
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None


def _image_content_block(provider: str, data_url: str) -> dict:
    if provider == "google":
        header, b64data = data_url.split(",", 1)
        mime = header.split(";")[0].split(":")[1]
        return {"inline_data": {"mime_type": mime, "data": b64data}}
    if provider == "anthropic":
        header, b64data = data_url.split(",", 1)
        mime = header.split(";")[0].split(":")[1]
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64data}}
    # local (OpenAI-совместимый) и openai — общий image_url формат
    return {"type": "image_url", "image_url": {"url": data_url}}


def png_bytes_to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def call_llm_json(
    config: LlmConfig,
    system_prompt: str,
    user_text: str,
    images: Optional[list[str]] = None,
    timeout: float = 120.0,
) -> Optional[dict]:
    """Синхронный structured-JSON вызов. images — список data-URL (png/jpeg)."""
    provider = config.provider
    model = config.resolved_model()
    images = images or []

    if provider == "local" or provider == "openai":
        base_url = config.resolved_base_url() if provider == "local" else "https://api.openai.com/v1"
        content: list[dict] = [{"type": "text", "text": user_text}]
        for img in images:
            content.append(_image_content_block(provider, img))
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content if images else user_text},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if provider == "openai":
            headers["Authorization"] = f"Bearer {config.api_key}"
        resp = httpx.post(f"{base_url}/chat/completions", json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return extract_json_object(text)

    if provider == "anthropic":
        content = [{"type": "text", "text": user_text}]
        for img in images:
            content.append(_image_content_block(provider, img))
        body = {
            "model": model,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        resp = httpx.post("https://api.anthropic.com/v1/messages", json=body, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        return extract_json_object(text)

    if provider == "google":
        parts = [{"text": user_text}]
        for img in images:
            parts.append(_image_content_block(provider, img))
        body = {
            "contents": [{"parts": parts}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"responseMimeType": "application/json"},
        }
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            f"?key={config.api_key}"
        )
        resp = httpx.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(text)

    raise ValueError(f"unknown provider: {provider}")
