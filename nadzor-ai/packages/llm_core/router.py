"""Выбор провайдера по политике безопасности, доступности и конфигурации.

Переключение провайдера — правка config/providers.yaml без пересборки.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from llm_core.ports import LLMProvider
from llm_core.providers.mock import MockProvider
from llm_core.providers.remote import (
    ClaudeProvider,
    GigaChatProvider,
    OpenAICompatProvider,
    YandexGPTProvider,
)

FACTORIES = {
    "gigachat": GigaChatProvider,
    "yandexgpt": YandexGPTProvider,
    "local_vllm": OpenAICompatProvider,
    "claude": ClaudeProvider,
}


class ProviderPolicyError(Exception):
    """Провайдер не допущен политикой безопасности."""


class ProviderRouter:
    """Роутер провайдеров. Единственная точка выбора модели в системе."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.providers_cfg: dict = config.get("providers", {})
        self.policy: dict = config.get("policy", {})
        self._cache: dict[str, LLMProvider] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> ProviderRouter:
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def get(self, name: str) -> LLMProvider:
        if name in self._cache:
            return self._cache[name]
        cfg = self.providers_cfg.get(name)
        if cfg is None:
            raise ProviderPolicyError(f"Провайдер «{name}» не описан в конфигурации.")
        if not cfg.get("enabled", False):
            raise ProviderPolicyError(f"Провайдер «{name}» отключён в конфигурации.")
        provider = MockProvider() if name == "mock" else FACTORIES[name](cfg)
        self._cache[name] = provider
        return provider

    def select(self, protected_object: bool = False, prefer: str | None = None) -> LLMProvider:
        """Выбрать провайдер для анализа с проверкой политики."""
        name = prefer or self.config.get("default", "mock")
        provider = self.get(name)
        if protected_object and self.policy.get("require_sovereign_for_protected_objects", True) \
                and not provider.is_sovereign:
            raise ProviderPolicyError(
                f"Провайдер «{provider.name}» не размещён в российском контуре и не может "
                "использоваться для объекта с повышенными требованиями к защите данных.")
        return provider

    def verifier(self, primary: LLMProvider, protected_object: bool = False) -> LLMProvider | None:
        """Провайдер перепроверки. Совпадение с основным допустимо только в демо."""
        name = self.config.get("verifier")
        if not name:
            return None
        try:
            provider = self.get(name)
        except ProviderPolicyError:
            return None
        if protected_object and not provider.is_sovereign:
            return None
        return provider

    def enabled_names(self) -> list[str]:
        return [n for n, c in self.providers_cfg.items() if c.get("enabled")]

    def describe(self) -> list[dict]:
        """Состояние провайдеров для экрана администратора."""
        out = []
        for name, cfg in self.providers_cfg.items():
            out.append({
                "name": name, "model": cfg.get("model", ""),
                "enabled": bool(cfg.get("enabled")),
                "is_sovereign": bool(cfg.get("is_sovereign")),
                "is_default": name == self.config.get("default"),
                "is_verifier": name == self.config.get("verifier"),
                "max_context_tokens": cfg.get("max_context_tokens", 0),
            })
        return out
