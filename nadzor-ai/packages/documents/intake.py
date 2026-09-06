"""Приём файлов: тип по сигнатуре, лимиты, собственный идентификатор.

Имя файла, присланное пользователем, никогда не участвует в формировании путей.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Protocol

from documents.schemas import IntakeError


class _AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...

# Сигнатуры (магические числа) разрешённых типов.
SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpeg", "image/jpeg"),
    (b"PK\x03\x04", "zip", "application/zip"),   # docx, xlsx и архивы
    (b"AutoCAD Binary DXF", "dxf", "image/vnd.dxf"),
]

_OOXML = "application/vnd.openxmlformats-officedocument"
OOXML_MARKERS = {
    "word/document.xml": ("docx", f"{_OOXML}.wordprocessingml.document"),
    "xl/workbook.xml": ("xlsx", f"{_OOXML}.spreadsheetml.sheet"),
}


def detect_type(head: bytes, body: bytes | None = None) -> tuple[str, str]:
    """Определить тип файла по сигнатуре. Расширение имени не используется."""
    for magic, kind, mime in SIGNATURES:
        if head.startswith(magic):
            if kind == "zip" and body is not None:
                return _refine_zip(body)
            return kind, mime
    if head.lstrip()[:1] in (b"0", b" ") and b"SECTION" in head:
        return "dxf", "image/vnd.dxf"
    raise IntakeError("Тип файла не распознан по сигнатуре. Загрузите PDF, DOCX, XLSX, "
                      "изображение или DXF.")


def _refine_zip(body: bytes) -> tuple[str, str]:
    """Различить docx, xlsx и обычный архив по содержимому контейнера."""
    for marker, (kind, mime) in OOXML_MARKERS.items():
        if marker.encode() in body[:65536] or marker.encode() in body[-65536:]:
            return kind, mime
    return "zip", "application/zip"


def check_limits(size_bytes: int, limits: dict) -> None:
    """Проверить размер до чтения файла целиком."""
    max_bytes = limits.get("max_file_bytes", 100 * 1024 * 1024)
    if size_bytes > max_bytes:
        raise IntakeError(
            f"Файл больше допустимого размера ({max_bytes // (1024 * 1024)} МБ). "
            "Разделите комплект на части или загрузите архив постранично.")
    if size_bytes == 0:
        raise IntakeError("Файл пустой.")


async def read_with_limit(file: _AsyncReadable, limits: dict, chunk_size: int = 1024 * 1024,
                          ) -> bytes:
    """Прочитать файл, прервавшись сразу по превышении лимита размера.

    `check_limits(len(data), ...)` после `file.read()` проверяет размер лишь
    тогда, когда файл уже целиком лежит в памяти — сам лимит уже не спасает
    от исчерпания ресурсов, которое он должен предотвращать. Здесь размер
    проверяется по мере чтения, поэтому от файла, превышающего лимит,
    в памяти остаётся не больше одного чанка сверх допустимого.
    """
    max_bytes = limits.get("max_file_bytes", 100 * 1024 * 1024)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise IntakeError(
                f"Файл больше допустимого размера ({max_bytes // (1024 * 1024)} МБ). "
                "Разделите комплект на части или загрузите архив постранично.")
    if total == 0:
        raise IntakeError("Файл пустой.")
    return b"".join(chunks)


def new_file_id() -> str:
    """Собственный идентификатор файла. Обход каталогов невозможен по построению."""
    return uuid.uuid4().hex


def storage_key(object_id: str, file_id: str, kind: str) -> str:
    """Ключ хранения. Собирается только из проверенных значений."""
    if not object_id.replace("-", "").isalnum() or not file_id.isalnum():
        raise IntakeError("Недопустимый идентификатор объекта или файла.")
    return f"objects/{object_id}/{file_id[:2]}/{file_id}.{kind}"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def accept(path: Path, limits: dict) -> dict:
    """Принять файл с диска: сигнатура, лимиты, хэш, идентификатор."""
    size = path.stat().st_size
    check_limits(size, limits)
    data = path.read_bytes()
    kind, mime = detect_type(data[:512], data)
    allowed = set(limits.get("allowed_signatures", []))
    if allowed and kind not in allowed:
        raise IntakeError(f"Файлы типа «{kind}» не принимаются в этом контуре.")
    file_id = new_file_id()
    return {
        "file_id": file_id,
        "kind": kind,
        "mime": mime,
        "size_bytes": size,
        "sha256": sha256_of(data),
        "original_name": path.name,
        "data": data,
    }
