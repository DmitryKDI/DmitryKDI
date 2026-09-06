"""Строгие схемы вывода модели (мера Б.3.2).

Ответ, не прошедший валидацию, дальше не идёт: повторная попытка, затем отказ
с записью в журнал аудита. Свободный текст отбрасывается.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class SchemaViolationError(Exception):
    """Ответ модели не соответствует схеме."""


class LLMElement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    mark: str = ""
    level: str = ""
    axes: str = ""
    section: str = ""


class LLMFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^D-\d{2}$")
    title: str = Field(min_length=8, max_length=300)
    severity: Literal["critical", "major", "minor"]
    confidence: float = Field(ge=0.0, le=1.0)
    element: LLMElement = LLMElement()
    fact_ids: list[str] = Field(min_length=1)     # без ссылки на факт вывод недопустим
    norm_clause: str = Field(min_length=3)        # без пункта норматива вывод недопустим
    rationale: str = Field(min_length=8, max_length=1200)


class LLMFindings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[LLMFinding] = Field(default_factory=list, max_length=50)


class LLMVerdict(BaseModel):
    """Результат перепроверки вывода вторым провайдером."""
    model_config = ConfigDict(extra="forbid")
    agreement: bool
    confidence: float = Field(ge=0.0, le=1.0)
    comment: str = Field(default="", max_length=600)


def parse_strict(raw_text: str, model: type[BaseModel]) -> BaseModel:
    """Разобрать ответ модели по схеме. Любое отклонение — отказ."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if not match:
            raise SchemaViolationError("Ответ модели не содержит объекта JSON.") from None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise SchemaViolationError("Ответ модели не разбирается как JSON.") from exc
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise SchemaViolationError(
            f"Ответ модели не соответствует схеме: {exc.error_count()} ошибок") from exc
