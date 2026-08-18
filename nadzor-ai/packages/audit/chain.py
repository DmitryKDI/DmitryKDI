"""Журнал аудита: только добавление, каждая запись хранит хэш предыдущей.

Попытка изменить или удалить запись обнаруживается проверкой цепочки.
В журнал не попадают содержимое документов, персональные данные и токены.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64

# Поля, которые никогда не сохраняются в журнале.
FORBIDDEN_KEYS = {"password", "token", "access_token", "secret", "authorization",
                  "snils", "content", "text", "raw_text", "file_bytes"}

# Маскирование персональных данных на уровне записи.
RE_FIO = re.compile(r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ])[а-яё]*\s+([А-ЯЁ])[а-яё]*(?:вич|вна|ич|на)?\b")
RE_EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b")


def mask_personal_data(value: Any) -> Any:
    """Заменить ФИО и адреса электронной почты на инициалы и маску."""
    if isinstance(value, str):
        masked = RE_FIO.sub(lambda m: f"{m.group(1)} {m.group(2)}. {m.group(3)}.", value)
        return RE_EMAIL.sub("[адрес скрыт]", masked)
    if isinstance(value, dict):
        return {k: mask_personal_data(v) for k, v in value.items() if k not in FORBIDDEN_KEYS}
    if isinstance(value, list):
        return [mask_personal_data(v) for v in value]
    return value


@dataclass
class AuditRecord:
    """Запись журнала."""
    seq: int
    occurred_at: str
    actor_id: str
    actor_role: str
    action: str
    entity_type: str
    entity_id: str
    payload: dict = field(default_factory=dict)
    prev_hash: str = GENESIS
    record_hash: str = ""

    def canonical(self) -> str:
        """Каноническое представление для хэширования."""
        body = {k: v for k, v in asdict(self).items() if k != "record_hash"}
        return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def compute_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


class AuditChain:
    """Цепочка записей журнала. Изменение любой записи ломает проверку."""

    def __init__(self, records: list[AuditRecord] | None = None) -> None:
        self._records: list[AuditRecord] = records or []

    def append(self, actor_id: str, actor_role: str, action: str, entity_type: str,
               entity_id: str = "", payload: dict | None = None) -> AuditRecord:
        """Добавить запись. Изменение и удаление не предусмотрены."""
        prev = self._records[-1].record_hash if self._records else GENESIS
        record = AuditRecord(
            seq=len(self._records) + 1,
            occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
            actor_id=actor_id, actor_role=actor_role, action=action,
            entity_type=entity_type, entity_id=entity_id,
            payload=mask_personal_data(payload or {}), prev_hash=prev,
        )
        record.record_hash = record.compute_hash()
        self._records.append(record)
        return record

    def verify(self) -> dict:
        """Проверить целостность цепочки."""
        prev = GENESIS
        for record in self._records:
            if record.prev_hash != prev:
                return {"valid": False, "broken_at": record.seq,
                        "reason": "нарушена связь с предыдущей записью"}
            if record.compute_hash() != record.record_hash:
                return {"valid": False, "broken_at": record.seq,
                        "reason": "содержимое записи изменено после сохранения"}
            prev = record.record_hash
        return {"valid": True, "records": len(self._records),
                "head": prev if self._records else GENESIS,
                "reason": "цепочка целостна"}

    def records(self, limit: int = 200, offset: int = 0) -> list[AuditRecord]:
        return list(reversed(self._records))[offset:offset + limit]

    def __len__(self) -> int:
        return len(self._records)
