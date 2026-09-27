"""Изоляция тестов от окружения машины.

Тест проверяет код, а не содержимое чужого диска или окружения. Раньше здесь
изолировались каталоги облачных ключей и сертификатов (Г.112): на машине
разработчика ключ был, и тесты «без ключа» проверяли не то, что написано в их
названиях. Облака больше нет, но принцип тот же — теперь изолируются адрес и
имя локальной модели и список разрешённых внутренних адресов.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_local_environment(monkeypatch):
    for name in ("NADZOR_LOCAL_LLM_URL", "NADZOR_LOCAL_LLM_MODEL", "NADZOR_ALLOWED_HOSTS",
                 "NADZOR_LLM_JSON_MODE", "NADZOR_LLM_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)
    yield
