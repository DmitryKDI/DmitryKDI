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
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx

DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

# Ollama обрезает контекст до 4096 токенов по умолчанию (параметр модели, не
# сервера) — двух картинок листа с промптом хватает, чтобы в это не влезть
# (реальный случай: 4300-5200 токенов на реальных чертежах при VISION_MAX_DIM
# прежних 1600px). На OpenAI-совместимом эндпоинте это лечится полем num_ctx
# верхнего уровня тела запроса — нестандартное расширение Ollama, OpenAI его
# не знает, поэтому только для provider == "local". Поднято вместе с
# VISION_MAX_DIM (vision.py) — более крупная картинка кодируется в
# ощутимо больше токенов, прежний запас мог не покрывать это с обеих
# картинок сразу.
LOCAL_NUM_CTX = 24576

PROVIDER_DEFAULT_MODELS = {
    # Сравнение листов идёт картинками (см. vision.compare_page_pair), поэтому
    # локальная модель по умолчанию обязана уметь зрение. Текстовая qwen3
    # молча возвращала бы отказ на каждый чертёж.
    "local": "qwen2.5vl:7b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5",
    "google": "gemini-2.5-flash",
    "yandexgpt": "yandexgpt/latest",
    # Базовая GigaChat-2 отвечает 422 "Model does not support image" на
    # vision-запрос (реальный случай) — раз сравнение листов всегда идёт
    # картинками, дефолт обязан быть Pro/Max-тиром, иначе каждое сравнение
    # молча падает.
    "gigachat": "GigaChat-2-Pro",
}

# GigaChat: OAuth-эндпоинт и сама API — разные хосты, оба фиксированы
# (Сбер), настраивать через LlmConfig.base_url незачем — переопределить
# можно точку API целиком через переменную окружения, если понадобится.
GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
# Целевой URL с 16 июля 2026 — единый для всех пользователей
GIGACHAT_API_BASE = os.environ.get("GIGACHAT_API_BASE", "https://api.giga.chat")
# Личный/бизнес — тариф аккаунта, не модели; задаётся авторизационным ключом.
GIGACHAT_SCOPE = os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
# TLS-сертификат хостов Сбера обычно подписан НУЦ Минцифры — вне России это
# не в системном доверенном наборе. Отключать проверку (verify=False) нельзя
# (действующая политика безопасности проекта) — вместо этого путь к
# сертификату задаётся явно через переменную окружения, если он нужен.
GIGACHAT_CA_BUNDLE = os.environ.get("GIGACHAT_CA_BUNDLE") or True

_gigachat_token_cache: dict[str, tuple[str, float]] = {}  # api_key -> (token, истекает_в_monotonic)


def _gigachat_token(api_key: str) -> str:
    """Токен живёт 30 минут — кэшируем по ключу с запасом в 10 минут, чтобы не
    получать новый на каждый вызов внутри одного прогона. api_key — это уже
    готовая Base64-строка "client_id:client_secret" (Authorization key из
    личного кабинета), передаётся в Basic как есть, без повторной кодировки."""
    cached = _gigachat_token_cache.get(api_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    resp = _post_json(
        GIGACHAT_OAUTH_URL,
        data={"scope": GIGACHAT_SCOPE},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {api_key}",
        },
        timeout=30.0, verify=GIGACHAT_CA_BUNDLE,
    )
    data = resp.json()
    token = data["access_token"]
    # expires_at приходит в мс от эпохи — переводим в остаток жизни от сейчас
    expires_at_ms = data.get("expires_at", int(time.time() * 1000) + 1800000)
    expires_in = max(60.0, expires_at_ms / 1000 - time.time())
    _gigachat_token_cache[api_key] = (token, time.monotonic() + expires_in - 600)
    return token


def _gigachat_upload_image(access_token: str, data_url: str) -> str:
    """Картинка передаётся не инлайн (как у OpenAI/Anthropic/Google), а через
    отдельную загрузку файла: POST /files -> id, id -> attachments сообщения.
    Другой контракт, не вариант общего inline image_url."""
    _, b64data = data_url.split(",", 1)
    raw = base64.b64decode(b64data)
    resp = _post_json(
        f"{GIGACHAT_API_BASE}/v1/files",
        files={"file": ("page.png", raw, "image/png")},
        data={"purpose": "general"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60.0, verify=GIGACHAT_CA_BUNDLE,
    )
    return resp.json()["id"]

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class LlmConfig:
    provider: str  # 'local' | 'openai' | 'anthropic' | 'google' | 'yandexgpt' | 'gigachat'
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
    # openai — единственный оставшийся потребитель этого формата (local
    # использует родной формат Ollama, см. call_llm_json)
    return {"type": "image_url", "image_url": {"url": data_url}}


def png_bytes_to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


def _post_json(url: str, **kwargs) -> httpx.Response:
    """httpx.post + raise_for_status, но с телом ответа в тексте ошибки —
    провайдер обычно объясняет причину 4xx (неверная модель, формат запроса),
    а голый код без текста превращает диагностику в гадание вслепую (реальный
    случай: 400 от Ollama на vision-запросе, причина ясна только из тела)."""
    resp = httpx.post(url, **kwargs)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise httpx.HTTPStatusError(f"{e}\nОтвет провайдера: {resp.text[:2000]}", request=e.request, response=e.response) from e
    return resp


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

    if provider == "local":
        # Ollama через свой родной /api/chat, не через OpenAI-совместимый
        # /v1/chat/completions: реальный случай на боевой машине — та ветка
        # молча теряла num_ctx (в исходниках Ollama ChatCompletionRequest
        # такого поля вообще нет, JSON просто отбрасывает незнакомый ключ),
        # поэтому каждое сравнение с двумя картинками листа падало 400-й
        # ошибкой по нехватке контекста. options.num_ctx у родного эндпоинта
        # поддерживается всегда и не зависит от версии совместимого слоя.
        root = config.resolved_base_url()
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        message: dict = {"role": "user", "content": user_text}
        if images:
            message["images"] = [img.split(",", 1)[1] if "," in img else img for img in images]
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, message],
            "format": "json",
            "stream": False,
            "options": {"num_ctx": LOCAL_NUM_CTX},
        }
        resp = _post_json(f"{root}/api/chat", json=body, headers={"Content-Type": "application/json"}, timeout=timeout)
        data = resp.json()
        return extract_json_object(data["message"]["content"])

    if provider == "openai":
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
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {config.api_key}"}
        resp = _post_json("https://api.openai.com/v1/chat/completions", json=body, headers=headers, timeout=timeout)
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
        resp = _post_json("https://api.anthropic.com/v1/messages", json=body, headers=headers, timeout=timeout)
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
        resp = _post_json(url, json=body, timeout=timeout)
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(text)

    if provider == "yandexgpt":
        # Ключ и Folder ID — из тех же переменных окружения, что и у
        # полной CRM (packages/llm_core/providers/remote.py): один .env,
        # один ключ, вводить его отдельно в этой панели не нужно.
        if images:
            raise ValueError(
                "YandexGPT здесь понимает только текст — для сравнения чертежей "
                "по картинке выберите локальную модель или другого провайдера"
            )
        api_key = os.environ.get("YANDEX_GPT_API_KEY", "")
        folder_id = os.environ.get("YANDEX_GPT_FOLDER_ID", "")
        if not api_key or not folder_id:
            raise ValueError("в .env не заданы YANDEX_GPT_API_KEY / YANDEX_GPT_FOLDER_ID")
        body = {
            "modelUri": f"gpt://{folder_id}/{model}",
            "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": "2000"},
            "messages": [
                {"role": "system", "text": system_prompt},
                {"role": "user", "text": user_text},
            ],
        }
        headers = {"Authorization": f"Api-Key {api_key}", "Content-Type": "application/json"}
        resp = _post_json(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            json=body, headers=headers, timeout=timeout,
        )
        data = resp.json()
        text = data["result"]["alternatives"][0]["message"]["text"]
        return extract_json_object(text)

    if provider == "gigachat":
        if not config.api_key:
            raise ValueError("не задан GigaChat api_key (авторизационный ключ, Base64 client_id:client_secret)")
        access_token = _gigachat_token(config.api_key)
        attachments = [_gigachat_upload_image(access_token, img) for img in images]
        message: dict = {"role": "user", "content": user_text}
        if attachments:
            message["attachments"] = attachments
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, message],
            "response_format": {"type": "json_object"},
        }
        resp = _post_json(
            f"{GIGACHAT_API_BASE}/v1/chat/completions",
            json=body, headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            timeout=timeout, verify=GIGACHAT_CA_BUNDLE,
        )
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return extract_json_object(text)

    raise ValueError(f"unknown provider: {provider}")
