import concurrent.futures
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.facts_store as facts_store


def _reset_store():
    try:
        if facts_store._engine is not None:
            facts_store._engine.dispose()
    except Exception:
        pass
    facts_store._engine = None
    facts_store._Session = None


def test_session_initialization_is_safe_under_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTS_STORE_DB", str(tmp_path / "facts.db"))
    _reset_store()

    def open_and_close(_):
        session = facts_store._session()
        try:
            assert session.bind is not None
            return str(session.bind.url)
        finally:
            session.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        urls = list(pool.map(open_and_close, range(48)))

    assert len(set(urls)) == 1
    assert facts_store._engine is not None
    assert facts_store._Session is not None
    _reset_store()


def test_session_recovers_if_engine_exists_but_factory_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTS_STORE_DB", str(tmp_path / "facts.db"))
    _reset_store()

    # This is the transient state that previously could be observed by a
    # second thread while the first thread was still initializing.
    from sqlalchemy import create_engine

    facts_store._engine = create_engine(
        f"sqlite:///{tmp_path / 'facts.db'}",
        connect_args={"check_same_thread": False},
    )
    facts_store._Session = None

    session = facts_store._session()
    try:
        assert session.bind is not None
        assert facts_store._Session is not None
    finally:
        session.close()
        _reset_store()
