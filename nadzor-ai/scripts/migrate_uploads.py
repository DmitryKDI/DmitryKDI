#!/usr/bin/env python3
"""Перенос ранее загруженных файлов в хранилище оригиналов (Г.109).

До появления хранилища каждая загрузка писалась отдельным файлом со
случайным именем, и одинаковое содержимое лежало столько раз, сколько раз
его загружали. Скрипт укладывает всё это в базу оригиналов: одинаковое
содержимое схлопывается по отпечатку, освободившееся место показывается
числом.

Ничего не удаляет без спроса: по умолчанию только считает и докладывает,
а удаляет исходные файлы лишь с `--remove`. Порядок обязателен — сначала
увидеть, сколько и что, потом решать.

Запуск:
    python scripts/migrate_uploads.py --dir packages/backend/uploads
    python scripts/migrate_uploads.py --dir packages/backend/uploads --remove
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app import file_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", required=True, help="каталог с ранее загруженными файлами")
    parser.add_argument("--remove", action="store_true",
                        help="удалить исходные файлы после укладки в хранилище")
    parser.add_argument("--limit", type=int, default=0, help="обработать не больше N файлов")
    args = parser.parse_args()

    root = Path(args.dir)
    if not root.is_dir():
        print(f"каталога нет: {root}", file=sys.stderr)
        raise SystemExit(2)

    files = sorted(root.glob("*.pdf"))
    if args.limit:
        files = files[: args.limit]
    print(f"найдено файлов: {len(files)}")

    seen: set[str] = set()
    total = duplicated = failed = 0
    for index, path in enumerate(files, 1):
        try:
            data = path.read_bytes()
        except Exception as exc:  # noqa: BLE001 — один файл не роняет перенос
            print(f"  пропущен ({exc}): {path.name}", file=sys.stderr)
            failed += 1
            continue
        digest = file_store.digest_of(data)
        if digest in seen:
            duplicated += len(data)
        seen.add(digest)
        total += len(data)
        file_store.put(data)
        if args.remove and path.stem != digest:
            path.unlink(missing_ok=True)
        if index % 100 == 0:
            print(f"  обработано {index} из {len(files)}…", flush=True)

    stats = file_store.stats()
    print(f"\nбыло файлов: {len(files)}, суммарно {total / 1073741824:.2f} ГБ")
    print(f"уникального содержимого: {stats.files}, {stats.bytes / 1073741824:.2f} ГБ")
    print(f"дубликаты занимали: {duplicated / 1073741824:.2f} ГБ")
    if failed:
        print(f"не прочитано файлов: {failed}")
    if not args.remove:
        print("\nисходные файлы НЕ удалены (нужен --remove)")


if __name__ == "__main__":
    main()
