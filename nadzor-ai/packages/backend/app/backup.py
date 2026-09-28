"""Резервное копирование баз и хранилища файлов (ТЗ 12, п.8; ТЗ 11: RPO ≤ 15 минут).

Все данные сервиса — три базы SQLite: процессы и решения, оригиналы
документов, память разбора страниц. Копия снимается штатным механизмом
SQLite (online backup): база не останавливается, копия согласована.

  • каждые 15 минут — база процессов и решений (то, что нельзя получить
    повторно: решения инспектора, протоколы, журнал аудита), хранится сутки;
  • ежедневно — все три базы, хранятся 30 дней.

Копии включаются каталогом `NADZOR_BACKUP_DIR`; без него резервное
копирование выключено, и это видно в `/api/v1/admin/backups`.
Шифрование копий «в покое» — средствами тома, на котором лежит каталог
(см. docs/threat-model.md).
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
from pathlib import Path

from . import facts_store, file_store
from .db import DB_PATH
from .observability import logger

FREQUENT_MINUTES = 15
FREQUENT_KEEP_HOURS = 24
DAILY_KEEP_DAYS = 30
_STAMP = "%Y%m%dT%H%M%S"


def backup_dir() -> Path | None:
    raw = os.environ.get("NADZOR_BACKUP_DIR", "").strip()
    return Path(raw) if raw else None


def _databases(full: bool) -> dict[str, Path]:
    bases = {"nadzor": Path(DB_PATH)}
    if full:
        bases["file_store"] = Path(file_store.STORE_PATH)
        bases["facts"] = Path(os.environ.get("FACTS_STORE_DB", str(facts_store.STORE_PATH)))
    return {name: path for name, path in bases.items() if path.exists()}


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    with sqlite3.connect(source) as src, sqlite3.connect(partial) as dst:
        src.backup(dst)
    partial.replace(target)  # копия появляется целиком или не появляется


def make(kind: str, now: dt.datetime | None = None) -> Path:
    """Снять копию: kind = 'frequent' (база процессов) или 'daily' (все базы)."""
    root = backup_dir()
    if root is None:
        raise RuntimeError("резервное копирование выключено: не задан NADZOR_BACKUP_DIR")
    now = now or dt.datetime.utcnow()
    folder = root / kind / now.strftime(_STAMP)
    for name, path in _databases(full=kind == "daily").items():
        _copy(path, folder / f"{name}.db")
    logger.info(f"резервная копия снята: {folder}", extra={"event": "backup"})
    return folder


def _snapshots(kind: str) -> list[tuple[dt.datetime, Path]]:
    root = backup_dir()
    if root is None or not (root / kind).is_dir():
        return []
    found = []
    for folder in (root / kind).iterdir():
        try:
            found.append((dt.datetime.strptime(folder.name, _STAMP), folder))
        except ValueError:
            continue  # чужой каталог не трогаем
    return sorted(found)


def prune(now: dt.datetime | None = None) -> int:
    """Удалить копии старше срока хранения. Возвращает число удалённых."""
    now = now or dt.datetime.utcnow()
    removed = 0
    for kind, keep in (("frequent", dt.timedelta(hours=FREQUENT_KEEP_HOURS)),
                       ("daily", dt.timedelta(days=DAILY_KEEP_DAYS))):
        for stamp, folder in _snapshots(kind):
            if now - stamp > keep:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
    return removed


def due(now: dt.datetime | None = None) -> list[str]:
    """Снять копии, срок которых подошёл; вернуть виды снятых копий."""
    if backup_dir() is None:
        return []
    now = now or dt.datetime.utcnow()
    made = []
    for kind, period in (("frequent", dt.timedelta(minutes=FREQUENT_MINUTES)),
                         ("daily", dt.timedelta(days=1))):
        existing = _snapshots(kind)
        if not existing or now - existing[-1][0] >= period:
            make(kind, now)
            made.append(kind)
    prune(now)
    return made


def status() -> dict:
    root = backup_dir()
    if root is None:
        return {"enabled": False, "reason": "не задан NADZOR_BACKUP_DIR"}
    result = {"enabled": True, "directory": str(root)}
    for kind in ("frequent", "daily"):
        snapshots = _snapshots(kind)
        result[kind] = {"count": len(snapshots),
                        "latest": snapshots[-1][0].isoformat() if snapshots else None}
    return result
