"""Общие фикстуры тестов."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("APP_ROOT", str(ROOT))


@pytest.fixture(scope="session")
def norms():
    from norms import load_norms
    return load_norms(ROOT / "data/norms/norms.json", ROOT / "data/norms/classifier.json")


@pytest.fixture(scope="session")
def demo_result():
    """Результат анализа основного демонстрационного объекта."""
    from scripts.analyze_demo import run_object
    return asyncio.run(run_object("OBJ-001"))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    """Приложение с отдельной базой на время тестов."""
    db = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db}"
    from fastapi.testclient import TestClient

    import api.main as main
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def auth(client):
    """Заголовки авторизации для учётной записи."""
    def _auth(subject: str) -> dict:
        token = client.post("/api/auth/login", json={"subject": subject}).json()
        return {"Authorization": "Bearer " + token["access_token"]}
    return _auth
