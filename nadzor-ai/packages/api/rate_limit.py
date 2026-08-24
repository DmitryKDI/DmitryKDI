"""Ограничение частоты запросов на загрузку и запуск анализа (Приложение Б.5).

Счётчики — в памяти одного процесса, как и остальное состояние api.state
(ProviderRouter, AuditChain). Этого достаточно для однопроцессного демо- и
продуктивного развёртывания фазы 1 (`uvicorn ... --host 0.0.0.0 --port 8000`
без `--workers`); при переходе на несколько воркеров или узлов счётчики
нужно вынести в общее хранилище (Redis и т.п.), иначе каждый процесс будет
считать частоту независимо.
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, status


class RateLimiter:
    """Скользящее окно: не больше `limit` попаданий за `window_seconds` на ключ."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def check(self, subject: str, bucket: str, limit: int, window_seconds: float) -> None:
        key = (subject, bucket)
        hits = self._hits[key]
        now = time.monotonic()
        cutoff = now - window_seconds
        while hits and hits[0] < cutoff:
            hits.pop(0)
        if len(hits) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много запросов за короткое время. Попробуйте позже.")
        hits.append(now)
