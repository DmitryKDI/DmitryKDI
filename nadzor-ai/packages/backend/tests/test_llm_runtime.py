"""Проверки общего бюджета запросов и адресации кэша без внешнего API."""
import sys
import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm_runtime import AdaptiveLimiter, ResultCache, request_cache_key, measure_run
from app import llm, llm_runtime


@pytest.fixture(autouse=True)
def fresh_runtime(monkeypatch):
    monkeypatch.setattr(llm_runtime, "GIGACHAT_LIMITER", AdaptiveLimiter(2))
    llm_runtime.RESULT_CACHE.clear()
    llm_runtime.IMAGE_CACHE.clear()
    llm._gigachat_token_cache.clear()
    monkeypatch.setattr(llm, "ca_bundle", lambda: True)


def config():
    return llm.LlmConfig("gigachat", base64.b64encode(b"synthetic:secret").decode(), model="model")


def fake_transport(monkeypatch, content='{"items": []}', finish_reason="stop"):
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs))
        if url == llm.GIGACHAT_OAUTH_URL:
            data = {"access_token": "synthetic-token"}
        elif url.endswith("/files"):
            data = {"id": "synthetic-image"}
        else:
            data = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)
    return requests


def test_cache_identity_changes_with_every_evidence_input():
    args = dict(account="account", model="model", operation="vision", prompt_version="v1",
                system="rules", text="requirement", images=["page"], source_digest="document")
    original = request_cache_key(**args)
    for name in args:
        changed = dict(args)
        changed[name] = ["different"] if name == "images" else "different"
        assert request_cache_key(**changed) != original, name
    print("OK: документ, изображение, требование, промпт, модель и аккаунт разделяют кэш")


def test_cached_result_is_independent_of_caller_mutations():
    cache = ResultCache(capacity=2)
    result = {"evidence": ["quote"]}
    cache.put("key", result)
    result["evidence"].clear()
    cached = cache.get("key")
    cached["evidence"].clear()
    assert cache.get("key") == {"evidence": ["quote"]}
    print("OK: обработка результата не изменяет доказательства в кэше")


def test_rate_limit_reduces_shared_concurrency():
    limiter = AdaptiveLimiter(2)
    limiter.rate_limited(0)
    assert limiter.limit == 1
    with limiter.slot():
        assert limiter.active == 1
    assert limiter.active == 0
    print("OK: 429 уменьшает общий лимит и слот освобождается")


def test_run_metrics_include_elapsed_time_and_attempts():
    from app.llm_runtime import record
    with measure_run("synthetic") as metrics:
        record("requests")
        record("retries")
        record("response_seconds", 0.5)
        record("responses")
    snapshot = metrics.snapshot()
    assert snapshot["requests"] == snapshot["retries"] == 1
    assert snapshot["mean_response_seconds"] == 0.5
    assert snapshot["elapsed_seconds"] >= 0
    print("OK: метрики прогона считают запросы, повторы, задержку и полное время")


def test_identical_request_reuses_result_but_changed_context_calls_provider(monkeypatch):
    calls = fake_transport(monkeypatch)
    for text in ("requirement A", "requirement A", "requirement B"):
        llm.call_llm_json(config(), "rules", text, operation="text_verify", source_digest="page")
    assert sum(url.endswith("/chat/completions") for url, _ in calls) == 2
    print("OK: повтор взят из кэша, новое требование на той же странице проверяется заново")


@pytest.mark.parametrize("content,finish", [("invalid", "stop"), ('{"items": []}', "length")])
def test_invalid_or_truncated_answer_is_not_cached_as_no_findings(monkeypatch, content, finish):
    calls = fake_transport(monkeypatch, content, finish)
    for _ in range(2):
        with pytest.raises(ValueError, match="не выполнена"):
            llm.call_llm_json(config(), "rules", "text", operation="extraction")
    assert sum(url.endswith("/chat/completions") for url, _ in calls) == 2
    print("OK: повреждённый и усечённый ответы дают ошибку и не кэшируются")


def test_image_upload_reused_across_different_requirements(monkeypatch):
    calls = fake_transport(monkeypatch)
    image = llm.png_bytes_to_data_url(b"synthetic pixels")
    with measure_run() as metrics:
        for text in ("requirement A", "requirement B"):
            llm.call_llm_json(config(), "rules", text, images=[image], operation="vision")
    assert sum(url.endswith("/files") for url, _ in calls) == 1
    assert metrics.snapshot()["image_cache_hits"] == 1
    assert metrics.snapshot()["image_uploads"] == 1
    print("OK: страница загружается однажды для нескольких проверок требований")


def test_operation_specific_response_budget_reaches_provider(monkeypatch):
    calls = fake_transport(monkeypatch)
    monkeypatch.setenv("NADZOR_LLM_CLASSIFICATION_MAX_TOKENS", "777")
    llm.call_llm_json(config(), "rules", "text", operation="classification")
    assert calls[-1][1]["json"]["max_tokens"] == 777
    print("OK: отдельный бюджет классификации передаётся провайдеру")


def test_limiter_bounds_parallel_requests_and_releases_after_exception():
    limiter = AdaptiveLimiter(2)
    inside = threading.Barrier(2)
    def work(_):
        with limiter.slot():
            inside.wait(timeout=5)
            assert limiter.active <= 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(work, range(2)))
    with pytest.raises(RuntimeError):
        with limiter.slot():
            raise RuntimeError("synthetic")
    assert limiter.active == 0
    print("OK: два независимых запроса работают параллельно, ошибка освобождает слот")


def test_parallel_map_propagates_metrics_context_and_keeps_order():
    def work(value):
        llm_runtime.record("requests")
        return value
    with measure_run() as metrics:
        assert list(llm_runtime.parallel_map(work, [3, None, 1])) == [3, None, 1]
    assert metrics.snapshot()["requests"] == 3
    print("OK: параллельные вызовы сохраняют порядок и метрики прогона")


def test_http_error_does_not_expose_response_body(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(422, text="sensitive document", request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError) as error:
        llm._post_json(llm.GIGACHAT_API_BASE + "/v1/chat/completions")
    assert "sensitive document" not in str(error.value)
    print("OK: исключение HTTP не раскрывает документ из ответа провайдера")


def test_long_retry_after_stops_without_early_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(429, headers={"Retry-After": "3600"},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)
    with measure_run() as metrics:
        with pytest.raises(RuntimeError):
            llm._post_json(llm.GIGACHAT_API_BASE + "/v1/chat/completions")
    assert len(calls) == 1
    assert llm_runtime.GIGACHAT_LIMITER.limit == 1
    assert metrics.snapshot()["rate_limits"] == 1
    assert metrics.snapshot()["retries"] == 0
    print("OK: длинный Retry-After не вызывает ранний повтор и снижает параллельность")


def test_identical_parallel_calls_are_single_flight(monkeypatch):
    calls = fake_transport(monkeypatch)
    barrier = threading.Barrier(2)
    def work(_):
        barrier.wait(timeout=5)
        return llm.call_llm_json(config(), "rules", "same text", operation="extraction")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(work, range(2)))
    assert results == [{"items": []}, {"items": []}]
    assert sum(url.endswith("/chat/completions") for url, _ in calls) == 1
    print("OK: одновременно запрошенный одинаковый результат оплачивается один раз")


def test_different_images_can_upload_in_parallel(monkeypatch):
    barrier = threading.Barrier(2)
    def post(url, **kwargs):
        barrier.wait(timeout=5)
        return httpx.Response(200, json={"id": "image"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda data: llm._gigachat_upload_image("token", data), [b"a", b"b"]))
    assert ids == ["image", "image"]
    print("OK: разные страницы загружаются параллельно в пределах общего лимита")


def test_expired_remote_file_gets_one_reupload(monkeypatch):
    counts = {"chat": 0, "upload": 0}
    def post(url, **kwargs):
        if url == llm.GIGACHAT_OAUTH_URL:
            payload = {"access_token": "token"}
        elif url.endswith("/files"):
            counts["upload"] += 1
            payload = {"id": str(counts["upload"])}
        else:
            counts["chat"] += 1
            if counts["chat"] == 1:
                return httpx.Response(404, request=httpx.Request("POST", url))
            assert kwargs["json"]["messages"][1]["attachments"] == ["2"]
            payload = {"choices": [{"message": {"content": '{"items": []}'}}]}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
    monkeypatch.setattr(llm.httpx, "post", post)
    result = llm.call_llm_json(config(), "rules", "text",
                               images=[llm.png_bytes_to_data_url(b"image")], operation="vision")
    assert result == {"items": []}
    assert counts == {"chat": 2, "upload": 2}
    print("OK: недоступный file_id обновляется одним повтором без подмены результата")
