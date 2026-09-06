"""Ограничение частоты запросов на загрузку и запуск анализа (Приложение Б.5)."""
from __future__ import annotations

import pytest


def test_rate_limiter_blocks_after_window_is_exhausted():
    from api.rate_limit import RateLimiter
    from fastapi import HTTPException

    limiter = RateLimiter()
    limiter.check("sudir:77001", "upload", limit=2, window_seconds=60)
    limiter.check("sudir:77001", "upload", limit=2, window_seconds=60)
    with pytest.raises(HTTPException) as info:
        limiter.check("sudir:77001", "upload", limit=2, window_seconds=60)
    assert info.value.status_code == 429


def test_rate_limiter_is_scoped_per_subject_and_bucket():
    from api.rate_limit import RateLimiter

    limiter = RateLimiter()
    limiter.check("sudir:77001", "upload", limit=1, window_seconds=60)
    # Другой сотрудник и другой вид действия — свой собственный счётчик.
    limiter.check("sudir:77002", "upload", limit=1, window_seconds=60)
    limiter.check("sudir:77001", "analysis_run", limit=1, window_seconds=60)


def test_upload_endpoint_is_rate_limited(client, auth):
    """После исчерпания лимита сервер отвечает 429, а не запускает разбор файла.

    Лимитер подменяется на свежий, а не только конфиг — иначе счётчик мог бы
    унаследовать попадания от других тестов, использующих того же сотрудника
    и в рамках того же процесса (state — общий на всю тестовую сессию).
    """
    from api.rate_limit import RateLimiter
    from api.state import state

    headers = auth("sudir:77001")
    original_limiter, original_limit = state.rate_limiter, state.limits_config["rate_limit"]["uploads_per_minute"]
    state.rate_limiter = RateLimiter()
    state.limits_config["rate_limit"]["uploads_per_minute"] = 2
    try:
        statuses = [
            client.post("/api/objects/OBJ-001/documents", headers=headers,
                       files={"file": ("note.txt", b"garbage", "text/plain")}).status_code
            for _ in range(3)
        ]
        assert 429 not in statuses[:2]
        assert statuses[2] == 429
    finally:
        state.rate_limiter = original_limiter
        state.limits_config["rate_limit"]["uploads_per_minute"] = original_limit


def test_analysis_run_endpoint_is_rate_limited(client, auth):
    from api.rate_limit import RateLimiter
    from api.state import state

    headers = auth("sudir:77001")
    original_limiter = state.rate_limiter
    original_limit = state.limits_config["rate_limit"]["analysis_runs_per_hour"]
    state.rate_limiter = RateLimiter()
    state.limits_config["rate_limit"]["analysis_runs_per_hour"] = 1
    try:
        first = client.post("/api/analysis/run", json={"object_id": "OBJ-001"}, headers=headers)
        second = client.post("/api/analysis/run", json={"object_id": "OBJ-001"}, headers=headers)
        assert first.status_code != 429
        assert second.status_code == 429
    finally:
        state.rate_limiter = original_limiter
        state.limits_config["rate_limit"]["analysis_runs_per_hour"] = original_limit
