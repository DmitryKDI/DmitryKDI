"""Хранилище оригиналов: дедупликация, срок хранения, кэш (Г.109)."""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["FILE_STORE_DB"] = tempfile.mktemp(suffix=".db")
os.environ["FILE_STORE_CACHE"] = tempfile.mkdtemp()

from app import file_store  # noqa: E402


def _fresh():
    file_store.STORE_PATH = Path(tempfile.mktemp(suffix=".db"))
    file_store.CACHE_DIR = Path(tempfile.mkdtemp())


def test_same_content_is_stored_once():
    """Ключ записи — отпечаток содержимого, поэтому один и тот же том,
    загруженный дважды, занимает место один раз. Это не оптимизация, а
    следствие устройства: двух записей с одним отпечатком не бывает."""
    _fresh()
    first = file_store.put(b"%PDF-1.4 same bytes", pages=5)
    second = file_store.put(b"%PDF-1.4 same bytes", pages=5)
    assert first == second
    assert file_store.stats().files == 1
    file_store.put(b"%PDF-1.4 other bytes", pages=1)
    assert file_store.stats().files == 2
    print("OK: одинаковое содержимое хранится один раз")


def test_sqlite_waits_on_locks_and_uses_wal():
    """Хранилище больших PDF должно ждать освобождения записи, а не падать на первой гонке."""
    _fresh()
    with file_store._session() as db:
        timeout_ms = db.execute(text("PRAGMA busy_timeout")).scalar_one()
        journal_mode = db.execute(text("PRAGMA journal_mode")).scalar_one()
    assert timeout_ms == file_store.SQLITE_BUSY_TIMEOUT_MS
    assert str(journal_mode).lower() == "wal"
    print("OK: SQLite в file_store ждёт блокировку и работает через WAL")


def test_cache_is_derived_and_restores_itself():
    """Кэш — производное от базы: удалили файл с диска, он восстановился при
    следующем обращении. Значит место можно освобождать не думая."""
    _fresh()
    digest = file_store.put(b"%PDF-1.4 payload")
    path = file_store.materialize(digest)
    assert path is not None and path.exists()
    path.unlink()
    again = file_store.materialize(digest)
    assert again is not None and again.exists()
    assert again.read_bytes() == b"%PDF-1.4 payload"
    print("OK: кэш восстанавливается из базы, база — источник истины")


def test_retention_never_removes_a_referenced_original():
    """Оригинал, на который ссылается документ, не удаляется ни по какому
    сроку: иначе инспектор откроет свой же документ и получит ошибку."""
    _fresh()
    kept = file_store.put(b"%PDF-1.4 referenced")
    gone = file_store.put(b"%PDF-1.4 forgotten")
    old = dt.datetime.utcnow() - dt.timedelta(days=400)
    with file_store._session() as db:
        for digest in (kept, gone):
            db.get(file_store.StoredFile, digest).used_at = old
        db.commit()

    removed, freed = file_store.purge_unused(days=90, keep={kept})
    assert removed == 1 and freed > 0
    assert file_store.read(kept) is not None, "файл со ссылкой удалён — этого нельзя"
    assert file_store.read(gone) is None
    print("OK: срок хранения не трогает оригиналы, на которые есть ссылки")


def test_retention_disabled_removes_nothing():
    """Нулевой срок означает «хранить бессрочно», а не «удалить всё»:
    администратор, обнуливший поле, не должен потерять комплект."""
    _fresh()
    digest = file_store.put(b"%PDF-1.4 payload")
    with file_store._session() as db:
        db.get(file_store.StoredFile, digest).used_at = dt.datetime(2000, 1, 1)
        db.commit()
    assert file_store.purge_unused(days=0) == (0, 0)
    assert file_store.read(digest) is not None
    print("OK: нулевой срок хранения ничего не удаляет")


def test_forget_removes_both_base_and_cache():
    _fresh()
    digest = file_store.put(b"%PDF-1.4 payload")
    path = file_store.materialize(digest)
    assert file_store.forget(digest) is True
    assert not path.exists()
    assert file_store.read(digest) is None
    print("OK: удаление уносит и запись, и копию в кэше")


if __name__ == "__main__":
    test_same_content_is_stored_once()
    test_sqlite_waits_on_locks_and_uses_wal()
    test_cache_is_derived_and_restores_itself()
    test_retention_never_removes_a_referenced_original()
    test_retention_disabled_removes_nothing()
    test_forget_removes_both_base_and_cache()
    test_two_writers_of_the_same_content_do_not_collide()
    print("ALL PASS")


def test_two_writers_of_the_same_content_do_not_collide():
    """Гонка при одновременной загрузке одного тома двумя инспекторами.

    Для хранилища по содержимому это не ошибка: ключ и есть отпечаток,
    значит запись, которую успел сделать другой, в точности та же. Найдено
    не рассуждением, а падением переноса, когда два процесса писали в одну
    базу одновременно.
    """
    _fresh()
    data = b"%PDF-1.4 concurrent"
    key = file_store.digest_of(data)
    original_get = file_store.StoredFile

    # Первый писатель уже уложил содержимое, а второй об этом ещё не знает:
    # воспроизводим именно это состояние, а не «пишем дважды подряд».
    with file_store._session() as db:
        db.add(original_get(digest=key, size=len(data), pages=0, content=data))
        db.commit()

    class _Blind(dict):
        def get(self, *_args, **_kwargs):
            return None

    real_session = file_store._session

    class _BlindSession:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            self._db = self._inner.__enter__()
            self._db.get = lambda *a, **k: None  # «не вижу существующей строки»
            return self._db

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    file_store._session = lambda: _BlindSession(real_session())
    try:
        assert file_store.put(data) == key, "гонка должна закончиться тем же ключом"
    finally:
        file_store._session = real_session
    assert file_store.read(key) == data
    print("OK: одновременная укладка одного содержимого не роняет хранилище")
