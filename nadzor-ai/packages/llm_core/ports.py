"""Порт языковой модели. Прямых вызовов SDK в бизнес-логике нет."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Vector = list[float]


@dataclass
class ImageBlock:
    """Отрисованный лист документа, приложенный к запросу для чтения зрением.

    Как и текст, изображение — данные документа, а не инструкция: провайдер
    без поддержки зрения обязан его игнорировать, а не пытаться прочитать.
    """
    media_type: str                            # image/png
    data_b64: str
    label: str = ""                             # для журналирования: сторона, лист, документ


@dataclass
class CompletionRequest:
    """Запрос к модели.

    Содержимое документа передаётся только в untrusted_blocks/images и
    оборачивается маркерами недоверенного контейнера. Инструкции живут в
    system и изменению из документа не подлежат.
    """
    task: str                                  # semantic_diff | verify | summarize
    system: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    norm_clauses: list[dict[str, Any]] = field(default_factory=list)
    untrusted_blocks: list[str] = field(default_factory=list)
    images: list[ImageBlock] = field(default_factory=list)
    prompt_version: str = "1.0"
    temperature: float = 0.0
    max_tokens: int = 2048
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompletionResponse:
    """Ответ модели. parsed заполняется только после валидации по схеме."""
    raw_text: str
    provider: str
    model: str
    prompt_version: str
    parsed: dict[str, Any] | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


@dataclass
class ProviderHealth:
    available: bool
    latency_ms: int = 0
    detail: str = ""


@runtime_checkable
class LLMProvider(Protocol):
    """Контракт провайдера. Реализации: GigaChat, YandexGPT, LocalVLLM, Claude, Mock."""

    name: str
    model: str
    supports_function_calling: bool
    supports_vision: bool       # может читать вложенные изображения листов
    max_context_tokens: int
    is_sovereign: bool          # размещён в РФ / реестр отечественного ПО

    async def complete(self, req: CompletionRequest) -> CompletionResponse: ...

    async def embed(self, texts: list[str]) -> list[Vector]: ...

    async def health(self) -> ProviderHealth: ...
