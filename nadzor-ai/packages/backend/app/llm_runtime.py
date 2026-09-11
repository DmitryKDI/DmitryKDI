"""Общие бюджеты и измерения LLM; метрики не содержат текстов и ключей."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from email.utils import parsedate_to_datetime
from pathlib import Path

# Бюджеты развёртывания, не пороги качества и не свойства документа.
DEFAULT_CONCURRENCY = 2
DEFAULT_CACHE_ENTRIES = 256
DEFAULT_CLASSIFICATION_TOKENS = 1024
DEFAULT_EXTRACTION_TOKENS = 4096
DEFAULT_TEXT_VERIFY_TOKENS = 4096
DEFAULT_VISION_TOKENS = 4096
DEFAULT_WAIT_BUDGET = 30.0


def positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def output_tokens(operation: str) -> int:
    defaults = {
        "classification": DEFAULT_CLASSIFICATION_TOKENS,
        "extraction": DEFAULT_EXTRACTION_TOKENS,
        "text_verify": DEFAULT_TEXT_VERIFY_TOKENS,
        "vision": DEFAULT_VISION_TOKENS,
    }
    default = defaults.get(operation, DEFAULT_EXTRACTION_TOKENS)
    return positive_env(f"NADZOR_LLM_{operation.upper()}_MAX_TOKENS", default)


class Metrics:
    def __init__(self):
        self.started = time.monotonic()
        self.finished = None
        self.values = defaultdict(float)
        self.lock = threading.Lock()

    def add(self, name, value=1):
        with self.lock:
            self.values[name] += value

    def snapshot(self):
        with self.lock:
            data = dict(self.values)
        for name in (
            "requests", "retries", "rate_limits", "errors", "responses",
            "image_uploads", "image_cache_hits", "result_cache_hits",
            "persistent_cache_hits", "text_characters", "text_batches",
            "invalid_results",
        ):
            data.setdefault(name, 0)
        data["mean_response_seconds"] = (
            data.get("response_seconds", 0) / data["responses"] if data["responses"] else None
        )
        data["mean_text_batch_characters"] = (
            data["text_characters"] / data["text_batches"] if data["text_batches"] else None
        )
        end = self.finished if self.finished is not None else time.monotonic()
        data["elapsed_seconds"] = end - self.started
        return data


PROCESS_METRICS = Metrics()
_RUN_METRICS = ContextVar("llm_run_metrics", default=None)


def record(name: str, value=1):
    PROCESS_METRICS.add(name, value)
    metrics = _RUN_METRICS.get()
    if metrics is not None:
        metrics.add(name, value)


@contextmanager
def measure_run(label: str = ""):
    metrics = Metrics()
    token = _RUN_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        metrics.finished = time.monotonic()
        _RUN_METRICS.reset(token)


class AdaptiveLimiter:
    """Единый лимит GigaChat с осторожным восстановлением после 429."""

    def __init__(self, limit: int):
        self.max_limit = max(1, limit)
        self.limit = min(2, self.max_limit)
        self.active = 0
        self.not_before = 0.0
        self.success_streak = 0
        self.condition = threading.Condition()

    def rate_limited(self, delay: float):
        with self.condition:
            self.limit = max(1, self.limit - 1)
            self.success_streak = 0
            self.not_before = max(self.not_before, time.monotonic() + delay)
            self.condition.notify_all()

    def succeeded(self):
        with self.condition:
            if self.limit >= self.max_limit:
                return
            self.success_streak += 1
            if self.success_streak >= max(4, self.limit * 3):
                self.limit += 1
                self.success_streak = 0
                self.condition.notify_all()

    @contextmanager
    def slot(self):
        with self.condition:
            while self.active >= self.limit or self.not_before > time.monotonic():
                wait_for = self.not_before - time.monotonic()
                if wait_for > DEFAULT_WAIT_BUDGET:
                    raise RuntimeError("Провайдер требует паузу; проверка пока не выполнена")
                self.condition.wait(timeout=wait_for if wait_for > 0 else None)
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


GIGACHAT_LIMITER = AdaptiveLimiter(
    positive_env("NADZOR_GIGACHAT_CONCURRENCY", DEFAULT_CONCURRENCY)
)


def parallel_map(function, items, workers: int | None = None):
    """Параллельная обработка без head-of-line blocking.

    Готовый worker сразу получает следующий элемент, даже если более ранний
    запрос ещё выполняется. Наружу результаты по-прежнему выдаются в исходном
    порядке, чтобы существующие callback-и и отчёты не меняли семантику.
    """
    budget = workers or positive_env("NADZOR_GIGACHAT_CONCURRENCY", DEFAULT_CONCURRENCY)
    iterator = enumerate(items)
    next_to_yield = 0
    ready = {}

    with ThreadPoolExecutor(max_workers=budget) as executor:
        pending = {}

        def submit_next() -> bool:
            try:
                index, item = next(iterator)
            except StopIteration:
                return False
            future = executor.submit(copy_context().run, function, item)
            pending[future] = index
            return True

        for _ in range(budget):
            if not submit_next():
                break

        while pending:
            completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in completed:
                index = pending.pop(future)
                ready[index] = future.result()
                submit_next()

            while next_to_yield in ready:
                yield ready.pop(next_to_yield)
                next_to_yield += 1


def retry_after_seconds(value: str | None, fallback: float) -> float:
    try:
        delay = float(value) if value else fallback
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = fallback
    import math
    return max(0.0, delay) if math.isfinite(delay) else fallback


def request_cache_key(**identity) -> str:
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ResultCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.values = OrderedDict()
        self.lock = threading.RLock()
        self.flights = {}

    @contextmanager
    def single_flight(self, key):
        with self.lock:
            entry = self.flights.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self.lock:
                entry[1] -= 1
                if not entry[1]:
                    del self.flights[key]

    def get(self, key):
        with self.lock:
            if key not in self.values:
                return None
            self.values.move_to_end(key)
            return copy.deepcopy(self.values[key])

    def put(self, key, value):
        with self.lock:
            self.values[key] = copy.deepcopy(value)
            self.values.move_to_end(key)
            while len(self.values) > self.capacity:
                self.values.popitem(last=False)

    def clear(self):
        with self.lock:
            self.values.clear()


class PersistentResultCache(ResultCache):
    """LRU в памяти + SQLite на диске для переживания рестартов."""

    def __init__(self, capacity: int, path: Path):
        super().__init__(capacity)
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS llm_results (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    def get(self, key):
        value = super().get(key)
        if value is not None:
            return value
        try:
            with self._connect() as db:
                row = db.execute(
                    "SELECT payload FROM llm_results WHERE cache_key = ?", (key,)
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        super().put(key, value)
        record("persistent_cache_hits")
        return copy.deepcopy(value)

    def put(self, key, value):
        super().put(key, value)
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            with self._connect() as db:
                db.execute(
                    """INSERT INTO llm_results(cache_key, payload, updated_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(cache_key) DO UPDATE SET
                           payload = excluded.payload,
                           updated_at = excluded.updated_at""",
                    (key, payload, time.time()),
                )
                disk_capacity = positive_env(
                    "NADZOR_LLM_PERSISTENT_CACHE_ENTRIES", max(2048, self.capacity * 8)
                )
                db.execute(
                    """DELETE FROM llm_results
                       WHERE cache_key IN (
                           SELECT cache_key FROM llm_results
                           ORDER BY updated_at DESC
                           LIMIT -1 OFFSET ?
                       )""",
                    (disk_capacity,),
                )
                db.commit()
        except (sqlite3.Error, TypeError, ValueError):
            return


_ROOT = Path(__file__).resolve().parents[3]
_RESULT_CACHE_DB = Path(
    os.environ.get("NADZOR_LLM_CACHE_DB", _ROOT / "data" / "llm_result_cache.sqlite3")
)

RESULT_CACHE = PersistentResultCache(
    positive_env("NADZOR_LLM_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES),
    _RESULT_CACHE_DB,
)
IMAGE_CACHE = ResultCache(positive_env("NADZOR_LLM_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES))
