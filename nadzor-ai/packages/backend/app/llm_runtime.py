"""Общие бюджеты и измерения LLM; метрики не содержат текстов и ключей."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from email.utils import parsedate_to_datetime

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
    defaults = {"classification": DEFAULT_CLASSIFICATION_TOKENS,
                "extraction": DEFAULT_EXTRACTION_TOKENS,
                "text_verify": DEFAULT_TEXT_VERIFY_TOKENS, "vision": DEFAULT_VISION_TOKENS}
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
        for name in ("requests", "retries", "rate_limits", "errors", "responses",
                     "image_uploads", "image_cache_hits", "result_cache_hits",
                     "text_characters", "text_batches", "invalid_results"):
            data.setdefault(name, 0)
        data["mean_response_seconds"] = (
            data.get("response_seconds", 0) / data["responses"] if data["responses"] else None)
        data["mean_text_batch_characters"] = (
            data["text_characters"] / data["text_batches"] if data["text_batches"] else None)
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
    """Вызывающий сохраняет snapshot вместе с прогоном; label не логируется."""
    metrics = Metrics()
    token = _RUN_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        metrics.finished = time.monotonic()
        _RUN_METRICS.reset(token)


class AdaptiveLimiter:
    """Единый лимит для всех потоков/прогонов в процессе; после 429 только снижается."""
    def __init__(self, limit: int):
        self.limit = max(1, limit)
        self.active = 0
        self.not_before = 0.0
        self.condition = threading.Condition()

    def rate_limited(self, delay: float):
        with self.condition:
            self.limit = max(1, self.limit - 1)
            self.not_before = max(self.not_before, time.monotonic() + delay)
            self.condition.notify_all()

    @contextmanager
    def slot(self):
        with self.condition:
            while self.active >= self.limit or self.not_before > time.monotonic():
                wait = self.not_before - time.monotonic()
                if wait > DEFAULT_WAIT_BUDGET:
                    raise RuntimeError("Провайдер требует паузу; проверка пока не выполнена")
                self.condition.wait(timeout=wait if wait > 0 else None)
            self.active += 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.condition.notify_all()


GIGACHAT_LIMITER = AdaptiveLimiter(positive_env("NADZOR_GIGACHAT_CONCURRENCY", DEFAULT_CONCURRENCY))


def parallel_map(function, items, workers: int | None = None):
    """Независимые операции с сохранением порядка и контекста метрик прогона.

    Worker не должен обращаться к сессии БД или вызывать UI callbacks.
    """
    budget = workers or positive_env("NADZOR_GIGACHAT_CONCURRENCY", DEFAULT_CONCURRENCY)
    iterator = iter(items)
    sentinel = object()
    with ThreadPoolExecutor(max_workers=budget) as executor:
        pending = []
        for _ in range(budget):
            item = next(iterator, sentinel)
            if item is sentinel:
                break
            pending.append(executor.submit(copy_context().run, function, item))
        while pending:
            yield pending.pop(0).result()
            item = next(iterator, sentinel)
            if item is not sentinel:
                pending.append(executor.submit(copy_context().run, function, item))


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
    """Ограниченный процессный кэш: данные исчезают при остановке сервера."""
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.values = OrderedDict()
        self.lock = threading.RLock()
        self.flights = {}

    @contextmanager
    def single_flight(self, key):
        """Одинаковый запрос оплачивается один раз, разные выполняются независимо."""
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


RESULT_CACHE = ResultCache(positive_env("NADZOR_LLM_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES))
IMAGE_CACHE = ResultCache(positive_env("NADZOR_LLM_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES))
