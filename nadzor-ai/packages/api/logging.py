"""Журналирование без персональных данных и содержимого документов.

Маскирование выполняется на уровне форматтера: даже случайно переданное
в сообщение значение в журнал в открытом виде не попадёт.
"""
from __future__ import annotations

import logging
import re

RE_FIO = re.compile(r"\b([А-ЯЁ][а-яё]{2,})\s+([А-ЯЁ])[а-яё]+\s+([А-ЯЁ])[а-яё]+(вич|вна|ична)\b")
RE_EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b")
RE_TOKEN = re.compile(r"(Bearer\s+|token[\"'=:\s]+)([A-Za-z0-9._\-:]{8,})", re.IGNORECASE)
RE_SNILS = re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b")


def mask(text: str) -> str:
    text = RE_FIO.sub(lambda m: f"{m.group(1)} {m.group(2)}. {m.group(3)}.", text)
    text = RE_EMAIL.sub("[адрес скрыт]", text)
    text = RE_TOKEN.sub(lambda m: f"{m.group(1)}[токен скрыт]", text)
    return RE_SNILS.sub("[идентификатор скрыт]", text)


class MaskingFormatter(logging.Formatter):
    """Форматтер, скрывающий персональные данные и секреты."""

    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(MaskingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
