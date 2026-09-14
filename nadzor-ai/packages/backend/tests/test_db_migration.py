"""Обновление инструмента не должно стирать введённый ключ и разобранные тома.

Г.113. База от прошлой версии пересобиралась целиком при любом расхождении
схемы. Обновление добавило несколько колонок — и вместе с историей прогонов
исчез ключ провайдера, введённый руками. На экране это выглядело как «ключ
ЛЛМ не задан»: причина (обновление снесло базу) в интерфейсе не видна вовсе,
а сообщение о пересборке уходило в консоль сервера, которую никто не читает.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db as db_module  # noqa: E402
from app.db import init_db  # noqa: E402


def _engine(tmp_path: Path):
    return create_engine(f"sqlite:///{tmp_path / 'old.db'}")


def _old_settings_table(engine, key: str) -> None:
    """База прошлой версии: настройки без колонок лимитов."""
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE settings (id INTEGER PRIMARY KEY, provider VARCHAR, "
            "base_url VARCHAR, model VARCHAR, api_key VARCHAR)"))
        conn.execute(text(
            "INSERT INTO settings (id, provider, base_url, model, api_key) "
            "VALUES (1, 'gigachat', '', '', :key)"), {"key": key})


def test_added_column_keeps_the_key_and_the_rows(tmp_path):
    engine = _engine(tmp_path)
    _old_settings_table(engine, "ключ-из-личного-кабинета")

    with patch.object(db_module, "engine", engine):
        assert db_module._schema_is_stale() is True
        init_db()
        assert db_module._schema_is_stale() is False

        with engine.begin() as conn:
            row = conn.execute(text(
                "SELECT api_key, retention_days, max_pages FROM settings")).mappings().one()

    assert row["api_key"] == "ключ-из-личного-кабинета", "ключ стёрт обновлением"
    assert row["retention_days"] == 90, "новая колонка без значения по умолчанию"
    assert row["max_pages"] == 5000, row
    print("OK: новая колонка дописывается, ключ и строки настроек остаются")


def test_documents_survive_a_new_column(tmp_path):
    """Не только настройки: разобранные тома тоже не должны исчезать."""
    engine = _engine(tmp_path)
    # Таблица прошлой версии: без digest/size/parts — ровно те поля, которые
    # добавило хранилище оригиналов и разрезание тяжёлых томов.
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, name VARCHAR, side VARCHAR, "
            "file_path VARCHAR, pages INTEGER, discipline_code VARCHAR, "
            "classification_source VARCHAR, status VARCHAR, uploaded_at DATETIME)"))
        conn.execute(text(
            "INSERT INTO documents (id, name, side, file_path, pages, status) "
            "VALUES (1, 'Том 5.2.1', 'before', '/tmp/t.pdf', 21, 'ok')"))

    with patch.object(db_module, "engine", engine):
        init_db()
        with engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT name, digest, parts FROM documents")).mappings().all()

    assert [r["name"] for r in rows] == ["Том 5.2.1"], rows
    assert rows[0]["digest"] == "", rows
    assert rows[0]["parts"] == "[]", "вычисляемое значение по умолчанию не подставлено"
    print("OK: добавление колонки не выбрасывает загруженные документы")


def test_rebuild_still_carries_the_settings_over(tmp_path):
    """Запасной путь: колонку дописать нельзя, схема пересобирается.

    Прогоны при этом теряются — они пересчитываются, — но ключ вводится
    руками, и терять его нельзя даже здесь.
    """
    engine = _engine(tmp_path)
    _old_settings_table(engine, "ключ-остаётся")

    with patch.object(db_module, "engine", engine), \
            patch.object(db_module, "_add_column_sql", return_value=None):
        init_db()

    with engine.begin() as conn:
        row = conn.execute(text("SELECT api_key, provider FROM settings")).mappings().one()
    assert row["api_key"] == "ключ-остаётся", "ключ потерян при пересборке схемы"
    assert row["provider"] == "gigachat", row
    print("OK: пересборка схемы переносит настройки, а не начинает с пустых")


def test_column_default_comes_from_the_model(tmp_path):
    """Значение для уже существующих строк берётся из модели, а не из воздуха."""
    engine = _engine(tmp_path)
    with patch.object(db_module, "engine", engine):
        table = db_module.Base.metadata.tables["settings"]
        sql = db_module._add_column_sql("settings", table.columns["retention_days"])
        text_sql = db_module._add_column_sql("settings", table.columns["provider"])

    assert sql is not None and sql.endswith("DEFAULT 90"), sql
    assert "NOT NULL" in sql, sql
    assert text_sql is not None and text_sql.endswith("DEFAULT 'gigachat'"), text_sql
    print("OK: значение по умолчанию берётся из модели, строка экранируется")


def test_fresh_database_is_created_from_scratch(tmp_path):
    """На пустом месте — обычное создание схемы, без обходных путей."""
    engine = _engine(tmp_path)
    with patch.object(db_module, "engine", engine):
        init_db()
        tables = set(inspect(engine).get_table_names())
    assert {"settings", "documents"} <= tables, tables
    print("OK: пустая база создаётся как обычно")


def test_db_path_is_overridable_by_environment():
    """Путь к базе задаётся переменной окружения — иначе стенд не переселить.

    Сравнивать с текущим окружением нельзя: путь считается один раз при
    импорте, а тесты меняют переменную позже. Проверяется сам источник.
    """
    source = Path(db_module.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("NADZOR_DB_PATH"' in source, "путь к базе прибит гвоздями"
    print("OK: путь к базе настраивается извне")
