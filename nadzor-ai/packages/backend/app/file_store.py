"""Хранилище ОРИГИНАЛОВ загруженных документов — отдельная база.

Зачем отдельная, а не вместе с разбором. Оригинал документа и результат его
разбора живут по разным правилам: разбор мал, читается постоянно и переживает
удаление файла; оригинал велик, читается редко и должен уметь исчезнуть по
сроку хранения, не задев результат. Держать их в одной базе — значит либо
таскать гигабайты при каждом обращении к разбору, либо не уметь удалить
оригинал отдельно (Б.5 — минимизация хранимого и срок хранения).

Второе: один файл вместо каталога. Перенести стенд на другую машину или
показать его целиком — это скопировать одну базу, а не каталог с тысячами
файлов, в котором ничего не понятно без БД.

Что хранится и чего НЕ хранится:
  * содержимое файла и его отпечаток (SHA-256), размер, число страниц;
  * имя файла НЕ хранится и в путь не попадает: имя даёт загружающая
    сторона, участвовать в построении пути оно не должно (Б.4), а как
    отображаемое поле оно уже лежит в записи документа.

Дедупликация по содержимому — не оптимизация, а следствие отпечатка:
ключ записи и есть отпечаток, поэтому один и тот же том, загруженный дважды
(двумя инспекторами, повторно после ошибки, в составе разных комплектов),
занимает место один раз, а ссылок на него столько, сколько нужно.

Работа с файлом на диске. Разбор читает документы через PyMuPDF по пути,
поэтому оригинал по требованию выкладывается в кэш (`materialize`). Кэш —
производное: его можно удалить целиком в любой момент, он восстановится из
базы. База — источник истины, каталог кэша — нет.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import DateTime, Integer, LargeBinary, String, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# Путь базы и кэша — переменные окружения, потому что на стенде, в тесте и на
# машине разработчика они разные, а код обязан быть один.
STORE_PATH = Path(os.environ.get(
    "FILE_STORE_DB",
    Path(__file__).resolve().parents[3] / "data" / "file_store.db"))
CACHE_DIR = Path(os.environ.get(
    "FILE_STORE_CACHE",
    Path(__file__).resolve().parents[1] / "uploads"))


class Base(DeclarativeBase):
    pass


class StoredFile(Base):
    __tablename__ = "files"

    digest: Mapped[str] = mapped_column(String, primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Когда файл в последний раз понадобился разбору. Срок хранения считается
    # от последнего обращения, а не от загрузки: комплект, с которым работают,
    # не должен исчезать посреди проверки.
    used_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


@dataclass
class StoreStats:
    files: int
    bytes: int


_engine = None
_Session = None
_engine_path: Path | None = None


def _session() -> Session:
    global _engine, _Session, _engine_path
    if _engine is None or _engine_path != STORE_PATH:
        STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{STORE_PATH}")
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine)
        _engine_path = STORE_PATH
    return _Session()


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(data: bytes, pages: int = 0) -> str:
    """Положить содержимое. Возвращает отпечаток — он же ключ и имя в кэше.

    Повторная укладка того же содержимого места не занимает: обновляется
    только отметка последнего обращения.
    """
    key = digest_of(data)
    with _session() as db:
        row = db.get(StoredFile, key)
        if row is None:
            db.add(StoredFile(digest=key, size=len(data), pages=pages, content=data))
            try:
                db.commit()
            except IntegrityError:
                # Гонка: то же содержимое уложил кто-то ещё между проверкой и
                # записью — два инспектора загрузили один том одновременно.
                # Для хранилища по содержимому это не ошибка: запись уже есть
                # и она в точности та же, потому что ключ и есть отпечаток.
                db.rollback()
            return key
        row.used_at = dt.datetime.utcnow()
        if pages and not row.pages:
            row.pages = pages
        db.commit()
    return key


def read(digest: str) -> bytes | None:
    with _session() as db:
        row = db.get(StoredFile, digest)
        if row is None:
            return None
        row.used_at = dt.datetime.utcnow()
        db.commit()
        return row.content


def materialize(digest: str) -> Path | None:
    """Выложить оригинал в кэш и вернуть путь. Кэш — производное от базы:
    файл на месте — отдаём его, нет — восстанавливаем из базы."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{digest}.pdf"
    if path.exists() and path.stat().st_size:
        with _session() as db:
            row = db.get(StoredFile, digest)
            if row is not None:
                row.used_at = dt.datetime.utcnow()
                db.commit()
        return path
    data = read(digest)
    if data is None:
        return None
    path.write_bytes(data)
    return path


def forget(digest: str) -> bool:
    """Удалить оригинал и его копию в кэше."""
    (CACHE_DIR / f"{digest}.pdf").unlink(missing_ok=True)
    with _session() as db:
        row = db.get(StoredFile, digest)
        if row is None:
            return False
        db.delete(row)
        db.commit()
        return True


def purge_unused(days: int, keep: set[str] | None = None) -> tuple[int, int]:
    """Удалить оригиналы, к которым не обращались `days` дней.

    `keep` — отпечатки, на которые ещё есть ссылки: удалять их нельзя,
    сколько бы времени ни прошло, иначе разбор перестанет открываться.
    Возвращает (сколько файлов, сколько байт).
    """
    if days <= 0:
        return (0, 0)
    edge = dt.datetime.utcnow() - dt.timedelta(days=days)
    keep = keep or set()
    removed = freed = 0
    with _session() as db:
        rows = db.scalars(select(StoredFile).where(StoredFile.used_at < edge)).all()
        for row in rows:
            if row.digest in keep:
                continue
            freed += row.size
            removed += 1
            (CACHE_DIR / f"{row.digest}.pdf").unlink(missing_ok=True)
            db.delete(row)
        db.commit()
    return (removed, freed)


def drop_cache(keep: set[str] | None = None) -> tuple[int, int]:
    """Очистить каталог кэша, не трогая базу: место освобождается сразу,
    а любой файл восстановится при следующем обращении."""
    keep = keep or set()
    removed = freed = 0
    if not CACHE_DIR.is_dir():
        return (0, 0)
    for path in CACHE_DIR.glob("*.pdf"):
        if path.stem in keep:
            continue
        freed += path.stat().st_size
        removed += 1
        path.unlink(missing_ok=True)
    return (removed, freed)


def stats() -> StoreStats:
    with _session() as db:
        files = db.scalar(select(func.count()).select_from(StoredFile)) or 0
        size = db.scalar(select(func.coalesce(func.sum(StoredFile.size), 0))) or 0
    return StoreStats(files=int(files), bytes=int(size))
