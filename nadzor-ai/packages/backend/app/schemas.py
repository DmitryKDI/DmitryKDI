from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    side: str
    pages: int
    discipline_code: str | None
    classification_source: str | None
    status: str
    uploaded_at: dt.datetime
    # Размер оригинала и число частей, на которые он разрезан для обработки.
    # Инспектору это говорит, почему тяжёлый том обрабатывается по кускам;
    # нумерация листов при этом остаётся исходной.
    size: int = 0
    parts_count: int = 0


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    base_url: str
    model: str
    retention_days: int = 90
    max_upload_kb: int = 512 * 1024
    max_pages: int = 5000
    part_kb: int = 32 * 1024


class SettingsUpdate(BaseModel):
    provider: str
    base_url: str = ""
    model: str = ""
    retention_days: int | None = None
    max_upload_kb: int | None = None
    max_pages: int | None = None
    part_kb: int | None = None


class LlmCheckOut(BaseModel):
    """Ответ предполётной проверки связи (Г.91) для кнопки в интерфейсе."""
    reachable: bool
    provider: str
    message: str
    # Транспорт до модели. У локальной модели это всегда внутренняя сеть
    # контура; поле оставлено отдельным, чтобы это было видно явно.
    tls: str = ""
    # Какая модель выбрана и загружена ли она на сервере модели. Техническое задание
    # запрещает менять модель, не сверившись с перечнем доступных, а
    # сверяться инспектору было не с чем. `model_available` трёхзначно:
    # None означает «перечень не получен», а не «модели нет» (Г.10).
    model: str = ""
    model_available: bool | None = None
    models_available: list[str] = []
    models_message: str = ""


