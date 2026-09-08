"""Изоляция тестов от локального окружения машины (Г.112).

Поводом стал реальный провал: после того как ключ стал подхватываться из
каталога `secrets/`, два теста «поведение без ключа» начали падать — на
машине разработчика ключ есть, и тесты внезапно проверяли не то, что
написано в их названиях.

То же касается сертификатов: набор из `certs/` меняет проверку TLS, а тест
не должен зависеть от того, что лежит в рабочей копии.

Поэтому каталоги ключей и сертификатов на время тестов переводятся на
пустые временные — тест проверяет код, а не содержимое чужого диска.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_local_environment(monkeypatch):
    from app import llm

    empty_secrets = Path(tempfile.mkdtemp())
    empty_certs = Path(tempfile.mkdtemp())
    monkeypatch.setattr(llm, "SECRETS_DIR", empty_secrets, raising=False)
    # Тесты сертификатов подменяют CERTS_DIR сами; здесь важно лишь то,
    # чтобы каталог рабочей копии не влиял на все остальные тесты.
    if "test_ca_bundle" not in os.environ.get("PYTEST_CURRENT_TEST", ""):
        monkeypatch.setattr(llm, "CERTS_DIR", empty_certs, raising=False)
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield
