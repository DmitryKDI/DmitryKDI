from app.benchmark_metrics import build_benchmark_summary, classification_metrics


def test_classification_metrics_counts_tp_fp_fn():
    metrics = classification_metrics(
        ["case-a", "case-b", "case-c"],
        ["case-a", "case-c", "noise"],
    )

    assert metrics.tp == 2
    assert metrics.fp == 1
    assert metrics.fn == 1
    assert metrics.precision == 0.666667
    assert metrics.recall == 0.666667
    assert metrics.f1 == 0.666667


def test_benchmark_summary_keeps_only_technical_llm_metrics():
    summary = build_benchmark_summary(
        ["a"],
        ["a"],
        duration_seconds=12.3456789,
        llm_metrics={
            "requests": 5,
            "errors": 1,
            "rate_limits": 2,
            "result_cache_hits": 3,
            "persistent_cache_hits": 4,
            "text_characters": 999999,
        },
    )

    assert summary["quality"]["f1"] == 1.0
    assert summary["duration_seconds"] == 12.345679
    assert summary["llm"] == {
        "requests": 5,
        "errors": 1,
        "rate_limits": 2,
        "result_cache_hits": 3,
        "persistent_cache_hits": 4,
    }
