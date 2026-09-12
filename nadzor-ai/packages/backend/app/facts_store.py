"""ПАМЯТЬ РАЗБОРА — извлечённые из документа факты, посчитанные один раз.

Зачем. Разбор страницы — это чтение PDF, разбор текстового слоя, реестры
помещений и оборудования, чтение штампа. На томе в сотни листов это минуты.
Раньше всё это считалось заново при каждом обращении: при загрузке, при
разборе ПД, при сверке, при построении реестра помещений РД — четыре раза
одна и та же работа над одним и тем же файлом. Здесь она делается один раз
и сохраняется.

Что это даёт сверке чертежей. Отличие на листе ищется не по картинке
целиком, а по разобранным частям листа: номера помещений, позиции
оборудования, баланс-рамки, штамп, вид листа. Когда эти части лежат
готовыми, сравнение двух комплектов — это сопоставление списков, а не
повторный разбор гигабайтов. Модели уходит не «вот два чертежа, найди
разницу», а короткий список того, что на них различается.

Ключ — отпечаток СОДЕРЖИМОГО (тот же SHA-256, что в `file_store`), а не имя
и не путь: тот же том, загруженный второй раз или под другим именем, уже
разобран. Вместе с отпечатком в ключ входит версия разборщика: меняется
код извлечения — прежние записи перестают подходить сами, без ручной
чистки и без риска отдать разбор по старым правилам (Г.10).

Отдельная база, а не общая с прогонами: разбор переживает и удаление
оригинала по сроку хранения, и пересборку схемы основной базы (Г.113).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .documents import DocumentFacts, extract_document_facts
from .yandex_ocr import is_configured as yandex_ocr_is_configured

# Версия разборщика. Поднимается при ЛЮБОМ изменении того, что и как
# извлекается из страницы: иначе сохранённый разбор молча отдавался бы по
# старым правилам, и новое поведение проверялось бы на старых данных (Г.10).
FACTS_VERSION = 2

STORE_PATH = Path(os.environ.get(
    "FACTS_STORE_DB",
    Path(__file__).resolve().parents[3] / "data" / "facts_store.db"))

_CHUNK = 1024 * 1024


class Base(DeclarativeBase):
    pass


class StoredFacts(Base):
    __tablename__ = "facts"

    digest: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    pages: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    used_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


_engine = None
_Session = None
# Background PD/compliance/analysis/triangulated tasks may initialize the facts
# store concurrently.  Without a lock, one thread can publish `_engine` before
# `_Session` is assigned; a second thread then skips initialization and calls
# `_Session()` while it is still None, yielding the exact opaque failure
# "'NoneType' object is not callable".  Keep initialization atomic.
_SESSION_INIT_LOCK = threading.RLock()


def _session() -> Session:
    """Подключение по требованию: путь берётся в момент обращения.

    Тест подменяет FACTS_STORE_DB на временный файл, и вычисление пути при
    импорте намертво связало бы модуль с базой машины разработчика.
    Инициализация engine+sessionmaker атомарна: несколько фоновых прогонов
    могут впервые обратиться к хранилищу одновременно.
    """
    global _engine, _Session
    path = Path(os.environ.get("FACTS_STORE_DB", str(STORE_PATH)))
    url = f"sqlite:///{path}"
    with _SESSION_INIT_LOCK:
        if _engine is None or _Session is None or str(_engine.url) != url:
            path.parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, connect_args={"check_same_thread": False})
            Base.metadata.create_all(bind=engine)
            session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            # Publish both objects only after both are ready.  The lock also
            # protects replacement when FACTS_STORE_DB changes in tests.
            _engine = engine
            _Session = session_factory
        session_factory = _Session
    if session_factory is None:  # defensive invariant; should be unreachable
        raise RuntimeError("facts store session factory is not initialized")
    return session_factory()


def digest_of_file(path: str | Path) -> str:
    """Отпечаток файла, посчитанный кусками.

    Кусками, а не целиком в память: том в сотни мегабайт читается ради
    шестнадцати байт ключа, и держать его в памяти незачем.
    """
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


def _to_payload(facts: DocumentFacts) -> str:
    return json.dumps({
        "text_facts": facts.text_facts,
        "room_facts": facts.room_facts,
        "page_kinds": facts.page_kinds,
        "equipment_facts": facts.equipment_facts,
        "balance_facts": facts.balance_facts,
        "sheet_info": facts.sheet_info,
        "excluded": facts.excluded,
        "ocr_status": facts.ocr_status,
        "ocr_pages_total": facts.ocr_pages_total,
        "ocr_pages_done": facts.ocr_pages_done,
        "ocr_text_pages": facts.ocr_text_pages,
        "ocr_errors": facts.ocr_errors,
    }, ensure_ascii=False)


def _from_payload(name: str, pages: int, payload: str) -> DocumentFacts:
    raw = json.loads(payload)
    # Ключи страниц в JSON становятся строками. Возвращаем целые: по всему
    # коду страница — число, и разнотипные ключи давали бы промах поиска,
    # который выглядит как «на листе ничего не найдено».
    return DocumentFacts(
        name=name,
        pages=pages,
        text_facts=raw.get("text_facts", []),
        room_facts=raw.get("room_facts", []),
        page_kinds={int(k): v for k, v in raw.get("page_kinds", {}).items()},
        equipment_facts=raw.get("equipment_facts", []),
        balance_facts=raw.get("balance_facts", []),
        sheet_info={int(k): v for k, v in raw.get("sheet_info", {}).items()},
        excluded={int(k): v for k, v in raw.get("excluded", {}).items()},
        ocr_status=str(raw.get("ocr_status") or "not_required"),
        ocr_pages_total=int(raw.get("ocr_pages_total") or 0),
        ocr_pages_done=[int(v) for v in raw.get("ocr_pages_done", [])],
        ocr_text_pages=[int(v) for v in raw.get("ocr_text_pages", [])],
        ocr_errors={int(k): str(v) for k, v in raw.get("ocr_errors", {}).items()},
    )


def stored(digest: str, *, touch: bool = True) -> DocumentFacts | None:
    """Готовый разбор по отпечатку, или None; отчёт с touch=False не меняет used_at."""
    with _session() as db:
        row = db.get(StoredFacts, (digest, FACTS_VERSION))
        if row is None:
            return None
        facts = _from_payload(row.name, row.pages, row.payload)
        if touch:
            row.used_at = dt.datetime.utcnow()
            db.commit()
        return facts


def put(digest: str, facts: DocumentFacts, *, replace: bool = False) -> None:
    """Сохранить разбор. Повторная запись того же ключа не ошибка."""
    with _session() as db:
        existing = db.get(StoredFacts, (digest, FACTS_VERSION))
        if existing is not None:
            if not replace:
                return
            existing.name = facts.name
            existing.pages = facts.pages
            existing.payload = _to_payload(facts)
            existing.used_at = dt.datetime.utcnow()
            db.commit()
            return
        db.add(StoredFacts(digest=digest, version=FACTS_VERSION, name=facts.name,
                           pages=facts.pages, payload=_to_payload(facts)))
        try:
            db.commit()
        except IntegrityError:
            # Тот же документ разбирают два прогона одновременно: обе записи
            # одинаковы по построению, и гонка здесь ничего не портит.
            db.rollback()


def facts_for(path: str | Path, name: str, digest: str | None = None) -> DocumentFacts:
    """Разбор документа: из памяти, а если его там нет — посчитать и сохранить.

    `digest` передаётся, если он уже посчитан (у загруженного документа он
    лежит в записи): считать отпечаток второй раз незачем.
    """
    key = digest or digest_of_file(path)
    remembered = stored(key)
    should_refresh_ocr = (
        remembered is not None
        and remembered.ocr_status == "not_configured"
        and yandex_ocr_is_configured()
    )
    if remembered is not None and not should_refresh_ocr:
        # Имя берётся из запроса, а не из памяти: один и тот же файл может
        # быть загружен под разными именами, а показывать нужно то, под
        # которым его загрузили сейчас.
        remembered.name = name
        return remembered
    facts = extract_document_facts(str(path), name)
    put(key, facts, replace=should_refresh_ocr)
    return facts


def forget(digest: str) -> None:
    with _session() as db:
        for version in db.scalars(select(StoredFacts.version)
                                  .where(StoredFacts.digest == digest)).all():
            row = db.get(StoredFacts, (digest, version))
            if row is not None:
                db.delete(row)
        db.commit()


def drop_outdated() -> int:
    """Удалить разборы прошлых версий разборщика. Возвращает число удалённых."""
    with _session() as db:
        rows = db.scalars(select(StoredFacts)
                          .where(StoredFacts.version != FACTS_VERSION)).all()
        for row in rows:
            db.delete(row)
        db.commit()
        return len(rows)


def stats() -> dict:
    with _session() as db:
        total = db.scalar(select(func.count()).select_from(StoredFacts)) or 0
        current = db.scalar(select(func.count()).select_from(StoredFacts)
                            .where(StoredFacts.version == FACTS_VERSION)) or 0
        size = db.scalar(select(func.sum(func.length(StoredFacts.payload)))) or 0
    return {"documents": int(total), "current_version": int(current),
            "bytes": int(size), "version": FACTS_VERSION}
