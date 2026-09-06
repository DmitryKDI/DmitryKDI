"""Провайдер-абстракция для LLM — по прямому решению пользователя (Г.71)
только два провайдера: Anthropic (Claude — «сам инструмент») и GigaChat.
Раньше здесь были ещё local/Ollama, OpenAI, Google, YandexGPT — убраны
по запросу «оставь только себя и гигачат», код и тесты под них
неиспользуемы, лишняя площадь для поддержки без реального применения в
этом продукте.

Порт AI_PROVIDERS/buildLlmRequest/callLlm/extractJsonObject из
nadzor-browser/main.js: тот же контракт structured-JSON вывода, который в
браузерном инструменте решил проблему рассуждающей модели, уходящей в
посторонний текст вместо ответа по задаче — здесь применяется и к тексту,
и к vision-запросам.
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

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
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
# По умолчанию — True (проверять). Отключить можно только явно:
# GIGACHAT_CA_BUNDLE=false (или 0, no).
_GIGACHAT_CA_RAW = os.environ.get("GIGACHAT_CA_BUNDLE", "")
if _GIGACHAT_CA_RAW.lower() in ("false", "0", "no"):
    GIGACHAT_CA_BUNDLE = False
else:
    GIGACHAT_CA_BUNDLE = _GIGACHAT_CA_RAW or True

_gigachat_token_cache: dict[str, tuple[str, float]] = {}  # api_key -> (token, истекает_в_monotonic)


def _gigachat_upload_image(access_token: str, png_bytes: bytes) -> str:
    """Загрузить картинку в хранилище GigaChat и вернуть file_id для attachments."""
    resp = _post_json(
        f"{GIGACHAT_API_BASE}/v1/files",
        files={"file": ("page.png", png_bytes, "image/png")},
        data={"purpose": "general"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60.0, verify=GIGACHAT_CA_BUNDLE,
    )
    return resp.json()["id"]


def _gigachat_token(client_id: str, client_secret: str) -> str:
    """Токен живёт 30 минут — кэшируем по паре client_id:client_secret с запасом
    в 10 минут, чтобы не получать новый на каждый вызов внутри одного прогона."""
    cache_key = f"{client_id}:{client_secret}"
    cached = _gigachat_token_cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    import base64
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = _post_json(
        GIGACHAT_OAUTH_URL,
        data={"scope": GIGACHAT_SCOPE},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {creds}",
        },
        timeout=30.0, verify=GIGACHAT_CA_BUNDLE,
    )
    data = resp.json()
    token = data["access_token"]
    expires_at_ms = data.get("expires_at", int(time.time() * 1000) + 1800000)
    expires_in = max(60.0, expires_at_ms / 1000 - time.time())
    _gigachat_token_cache[cache_key] = (token, time.monotonic() + expires_in - 600)
    return token

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class LlmConfig:
    provider: str  # 'anthropic' | 'gigachat'
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def resolved_model(self) -> str:
        return self.model or PROVIDER_DEFAULT_MODELS.get(self.provider, "")

    def resolved_base_url(self) -> str:
        return self.base_url


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


def _anthropic_image_block(data_url: str) -> dict:
    header, b64data = data_url.split(",", 1)
    mime = header.split(";")[0].split(":")[1]
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64data}}


def png_bytes_to_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")


# Г.78 — реальный найденный сбой: пачка последовательных вызовов
# (requirement_text_verify.py делает по вызову на КАЖДУЮ оставшуюся
# страницу РД для каждого требования — на комплекте с 53+ требованиями и
# десятком страниц это сотни вызовов подряд) упирается в лимит частоты
# GigaChat раньше, чем в реальный сбой сети — 429 у КР/НВ (1540 и 14
# сбоев) на том же прогоне, где ОВ/АР с меньшим числом вызовов отработали
# чисто. Без ретрая это неотличимо от честного "провайдер недоступен".
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY = 2.0


def _post_json(url: str, **kwargs) -> httpx.Response:
    """httpx.post + raise_for_status, но с телом ответа в тексте ошибки —
    провайдер обычно объясняет причину 4xx (неверная модель, формат запроса),
    а голый код без текста превращает диагностику в гадание вслепую (реальный
    случай: 400 от Ollama на vision-запросе, причина ясна только из тела).

    429 (Too Many Requests) — отдельная ветка: несколько попыток с
    задержкой (`Retry-After` от провайдера, если есть, иначе экспоненциально
    растущая пауза) вместо немедленного отказа — см. Г.78."""
    attempt = 0
    while True:
        resp = httpx.post(url, **kwargs)
        if resp.status_code == 429 and attempt < _RATE_LIMIT_MAX_RETRIES:
            retry_after = resp.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            except ValueError:
                delay = _RATE_LIMIT_BASE_DELAY * (2 ** attempt)
            time.sleep(delay)
            attempt += 1
            continue
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

    if provider == "anthropic":
        content = [{"type": "text", "text": user_text}]
        for img in images:
            content.append(_anthropic_image_block(img))
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

    if provider == "gigachat":
        # GigaChat 2 про: клиентские реквизиты в формате Client ID:Client Secret
        if not config.api_key:
            raise ValueError("не задан GigaChat api_key (Base64-строка Client ID:Client Secret)")
        import base64
        try:
            decoded = base64.b64decode(config.api_key).decode()
            parts = decoded.split(":", 1)
            if len(parts) != 2:
                raise ValueError("api_key должен быть Base64 от 'client_id:client_secret'")
            client_id, client_secret = parts
        except Exception as exc:
            raise ValueError(f"Не удалось разобрать api_key: {exc}") from None
        access_token = _gigachat_token(client_id, client_secret)
        # GigaChat не поддерживает response_format и inline image_url —
        # изображения загружаются в хранилище через /files и передаются как attachments.
        attachments = []
        if images:
            for img in images:
                _, b64data = img.split(",", 1)
                png_bytes = base64.b64decode(b64data)
                file_id = _gigachat_upload_image(access_token, png_bytes)
                attachments.append(file_id)
        message: dict = {"role": "user", "content": user_text}
        if attachments:
            message["attachments"] = attachments
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, message],
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
