"""Загрузка .env без внешних зависимостей (важно для совместимости с Termux)."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(__file__).parent / ".env"


def load_env(path: str | Path = DEFAULT_ENV_PATH) -> None:
    path = Path(path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
