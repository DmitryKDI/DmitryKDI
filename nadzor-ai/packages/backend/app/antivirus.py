"""Антивирусная проверка загружаемых файлов (ТЗ 12, п.11).

Каждый файл проверяется ДО сохранения в хранилище. Проверку выполняет
антивирус контура по протоколу clamd (команда INSTREAM) — адрес задаётся
`NADZOR_CLAMD_HOST` (и `NADZOR_CLAMD_PORT`). Новых зависимостей нет:
протокол — несколько байт через сокет.

Если антивирус задан, но недоступен, файл НЕ принимается: пропустить файл
без проверки значило бы выдать отсутствие проверки за её успех (Г.10).
Если антивирус не задан, файл принимается, а в ответе видно, что проверка
не выполнялась.
"""
from __future__ import annotations

import os
import socket
import struct
from dataclasses import dataclass

HOST_ENV = "NADZOR_CLAMD_HOST"
PORT_ENV = "NADZOR_CLAMD_PORT"
DEFAULT_PORT = 3310
TIMEOUT_S = 60
# Размер куска потока INSTREAM, байт.
CHUNK = 1 << 16

CLEAN = "CLEAN"
INFECTED = "INFECTED"
UNAVAILABLE = "UNAVAILABLE"
NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class ScanResult:
    status: str
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.status in {CLEAN, NOT_CONFIGURED}


def scan(data: bytes) -> ScanResult:
    host = os.environ.get(HOST_ENV, "").strip()
    if not host:
        return ScanResult(NOT_CONFIGURED, "антивирус контура не подключён")
    try:
        port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    except ValueError:
        port = DEFAULT_PORT
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_S) as sock:
            sock.sendall(b"zINSTREAM\0")
            for start in range(0, len(data), CHUNK):
                piece = data[start:start + CHUNK]
                sock.sendall(struct.pack("!L", len(piece)) + piece)
            sock.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as exc:
        return ScanResult(UNAVAILABLE, f"антивирус недоступен: {exc}")
    text = reply.rstrip(b"\0").decode("utf-8", "replace")
    if text.endswith("OK"):
        return ScanResult(CLEAN)
    if text.endswith("FOUND"):
        return ScanResult(INFECTED, text.split(":", 1)[-1].strip())
    return ScanResult(UNAVAILABLE, f"неожиданный ответ антивируса: {text[:200]}")
