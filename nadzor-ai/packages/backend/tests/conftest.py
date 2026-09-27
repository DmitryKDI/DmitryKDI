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


@pytest.fixture(autouse=True)
def isolate_run_logs(tmp_path_factory, monkeypatch):
    """Логи прогонов тестов — во временный каталог, а не в рабочий.

    Фоновые задачи пишут диагностику при каждом прогоне, и без изоляции
    каталог `data/run_logs` рабочей копии заполняется тестовыми снимками.
    Дальше их читает разбор диагностики — и тестовый мусор выглядит как
    настоящие прогоны инспектора.
    """
    from app import run_logger

    root = tmp_path_factory.mktemp("run_logs")
    monkeypatch.setattr(run_logger, "RUN_LOGS_DIR", root, raising=False)
    monkeypatch.setattr(run_logger, "TASK_LOGS_DIR", root / "tasks", raising=False)
    yield
