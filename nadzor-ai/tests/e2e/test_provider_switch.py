"""Переключение провайдера не должно ронять анализ.

Переключение через настоящий /admin/providers/default пишет выбор на диск
(state.save_providers_config) — здесь конфигурация подменяется только в
памяти теста и возвращается обратно, не трогая config/providers.yaml
в репозитории.
"""
from __future__ import annotations

from api.state import state
from llm_core.router import ProviderRouter


def test_missing_yandex_credentials_degrades_to_rules_not_crash(client, auth, monkeypatch):
    """packages/analysis/llm_layer.py и verification.py гасят любой сбой
    провайдера и тихо деградируют до правил (комментарий в коде: «недоступность
    провайдера не роняет анализ») — здесь это проверяется реальным запуском:
    без ключа анализ обязан вернуть 200 и находки от детерминированных правил,
    а не 500."""
    monkeypatch.delenv("YANDEX_GPT_API_KEY", raising=False)
    monkeypatch.delenv("YANDEX_GPT_FOLDER_ID", raising=False)

    original_default = state.providers_config["default"]
    state.providers_config["default"] = "yandexgpt"
    state.router = ProviderRouter(state.providers_config)  # только в памяти, на диск не пишем
    try:
        resp = client.post("/api/analysis/run",
                           json={"object_id": "OBJ-001", "transitions": ["T1"]},
                           headers=auth("sudir:77001"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["findings"] > 0, "детерминированные правила не должны зависеть от LLM"
        print(f"OK: без реквизитов Yandex анализ всё равно вернул "
              f"{resp.json()['findings']} находок от правил, без падения")
    finally:
        state.providers_config["default"] = original_default
        state.router = ProviderRouter(state.providers_config)


def test_unknown_provider_name_gives_422_not_500(client, auth, monkeypatch):
    """Единственная ошибка выбора провайдера, которая реально долетает до
    обработчика непойманной (см. комментарий в routers/analysis.py) —
    несуществующее или отключённое имя в конфиге."""
    original_default = state.providers_config["default"]
    state.providers_config["default"] = "no-such-provider"
    state.router = ProviderRouter(state.providers_config)
    try:
        resp = client.post("/api/analysis/run",
                           json={"object_id": "OBJ-001", "transitions": ["T1"]},
                           headers=auth("sudir:77001"))
        assert resp.status_code == 422, resp.text
        assert "no-such-provider" in resp.json()["detail"]
        print(f"OK: неизвестный провайдер — 422 с понятным текстом, "
              f"не 500: «{resp.json()['detail']}»")
    finally:
        state.providers_config["default"] = original_default
        state.router = ProviderRouter(state.providers_config)
