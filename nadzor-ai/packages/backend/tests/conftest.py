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


# Запросы тестов по умолчанию идут от имени администратора: тест проверяет
# механику эндпоинта, а не вход. Вход и разграничение прав проверяются
# отдельно — тесты с меткой real_auth идут через настоящую аутентификацию.
TEST_ADMIN_ID = 1


def principal(role: str = "admin", user_id: int = TEST_ADMIN_ID):
    from app import auth
    return auth.Principal(user_id, f"test-{role}", role, f"Тест ({role})")


@pytest.fixture(autouse=True)
def authenticated(request):
    if "real_auth" in request.keywords:
        yield
        return
    from app import auth
    from app.main import app
    app.dependency_overrides[auth.current_user] = lambda: principal()
    yield
    app.dependency_overrides.pop(auth.current_user, None)


@pytest.fixture
def act_as():
    """Сменить роль пользователя тестового запроса."""
    from app import auth
    from app.main import app

    def use(role: str) -> None:
        app.dependency_overrides[auth.current_user] = lambda: principal(role, user_id=2)

    return use
