"""Адаптеры внешних провайдеров.

Реквизиты API не выдумываются: параметры вынесены в конфиг и переменные
окружения, спорные места перечислены в docs/integration-questions.md.
Адаптеры не проверялись на реальных стендах — до подключения ключей
работает офлайн-заглушка.
"""
from __future__ import annotations

import os
import time
import uuid

import httpx

from llm_core.envelope import SYSTEM_RULES, build_user_message
from llm_core.ports import CompletionRequest, CompletionResponse, ProviderHealth, Vector


def _messages(req: CompletionRequest) -> tuple[str, str]:
    """Системное сообщение и пользовательское сообщение с недоверенным контейнером."""
    system = f"{SYSTEM_RULES}\n\n{req.system}".strip()
    user = build_user_message(req.facts, req.norm_clauses, req.untrusted_blocks,
                              req.context.get("instruction", "Верни JSON по схеме."))
    return system, user


class _Base:
    """Общая часть адаптеров: таймауты, замер времени, единый ответ."""

    name = "base"
    model = ""
    supports_function_calling = False
    max_context_tokens = 32000
    is_sovereign = False

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.model = cfg.get("model", self.model)
        self.base_url = cfg.get("base_url", "")
        self.max_context_tokens = cfg.get("max_context_tokens", self.max_context_tokens)
        self.is_sovereign = cfg.get("is_sovereign", self.is_sovereign)
        self.timeout = cfg.get("timeout_seconds", 60)

    def _response(self, text: str, req: CompletionRequest, started: float,
                  usage: dict | None = None) -> CompletionResponse:
        usage = usage or {}
        return CompletionResponse(
            raw_text=text, provider=self.name, model=self.model,
            prompt_version=req.prompt_version,
            tokens_in=int(usage.get("prompt_tokens", 0)),
            tokens_out=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.monotonic() - started) * 1000))

    async def embed(self, texts: list[str]) -> list[Vector]:
        raise NotImplementedError("Векторизация выполняется отдельным сервисом норм.")

    async def health(self) -> ProviderHealth:
        if not self.base_url:
            return ProviderHealth(available=False, detail="не задан адрес сервиса")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(self.base_url)
            return ProviderHealth(available=True)
        except httpx.HTTPError as exc:
            return ProviderHealth(available=False, detail=str(exc)[:120])


class OpenAICompatProvider(_Base):
    """Провайдер с интерфейсом, совместимым с OpenAI Chat Completions.

    Используется для локального vLLM в изолированном контуре.
    """

    name = "local_vllm"
    is_sovereign = True

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        started = time.monotonic()
        system, user = _messages(req)
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=payload)
            r.raise_for_status()
            data = r.json()
        text = data["choices"][0]["message"]["content"]
        return self._response(text, req, started, data.get("usage"))


class GigaChatProvider(_Base):
    """GigaChat. Доступ по OAuth-токену, выдаваемому по клиентским реквизитам."""

    name = "gigachat"
    is_sovereign = True
    _token: str = ""
    _token_expires: float = 0.0

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 30:
            return self._token
        creds = os.environ.get(self.cfg.get("credentials_env", "GIGACHAT_CREDENTIALS"), "")
        if not creds:
            raise RuntimeError("Не заданы реквизиты доступа к GigaChat.")
        url = self.cfg.get("oauth_url", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
        headers = {"Authorization": f"Basic {creds}", "RqUID": str(uuid.uuid4()),
                   "Content-Type": "application/x-www-form-urlencoded"}
        scope = self.cfg.get("scope", "GIGACHAT_API_CORP")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=headers, data={"scope": scope})
            r.raise_for_status()
            data = r.json()
        self._token = data["access_token"]
        self._token_expires = data.get("expires_at", time.time() + 1500) / 1000
        return self._token

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        started = time.monotonic()
        system, user = _messages(req)
        token = await self._access_token()
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "temperature": max(req.temperature, 0.01),
                   "max_tokens": req.max_tokens}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=payload,
                                  headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json()
        return self._response(data["choices"][0]["message"]["content"], req, started,
                              data.get("usage"))


class YandexGPTProvider(_Base):
    """YandexGPT Foundation Models."""

    name = "yandexgpt"
    is_sovereign = True

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        started = time.monotonic()
        system, user = _messages(req)
        api_key = os.environ.get(self.cfg.get("api_key_env", "YANDEX_GPT_API_KEY"), "")
        folder = os.environ.get(self.cfg.get("folder_id_env", "YANDEX_GPT_FOLDER_ID"), "")
        if not api_key or not folder:
            raise RuntimeError("Не заданы реквизиты доступа к YandexGPT.")
        payload = {
            "modelUri": f"gpt://{folder}/{self.model}",
            "completionOptions": {"stream": False, "temperature": req.temperature,
                                  "maxTokens": str(req.max_tokens)},
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base_url}/completion", json=payload,
                                  headers={"Authorization": f"Api-Key {api_key}"})
            r.raise_for_status()
            data = r.json()
        result = data["result"]
        usage = result.get("usage", {})
        return self._response(result["alternatives"][0]["message"]["text"], req, started,
                              {"prompt_tokens": usage.get("inputTextTokens", 0),
                               "completion_tokens": usage.get("completionTokens", 0)})


class ClaudeProvider(_Base):
    """Claude. Только разработка и офлайн-демо на синтетических данных.

    Не является отечественным программным обеспечением: для продуктивного
    контура запрещён политикой ProviderRouter.
    """

    name = "claude"
    is_sovereign = False
    max_context_tokens = 200000

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        started = time.monotonic()
        system, user = _messages(req)
        api_key = os.environ.get(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        if not api_key:
            raise RuntimeError("Не задан ключ доступа.")
        payload = {"model": self.model, "max_tokens": req.max_tokens,
                   "temperature": req.temperature, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            base = self.cfg.get("base_url", "https://api.anthropic.com")
            r = await client.post(f"{base}/v1/messages", json=payload,
                                  headers={"x-api-key": api_key,
                                           "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            data = r.json()
        usage = data.get("usage", {})
        return self._response(data["content"][0]["text"], req, started,
                              {"prompt_tokens": usage.get("input_tokens", 0),
                               "completion_tokens": usage.get("output_tokens", 0)})
