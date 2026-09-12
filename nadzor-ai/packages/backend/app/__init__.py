"""Backend package bootstrap.

Local development is commonly started with ``python -m uvicorn ...`` rather
than the shell launcher. Uvicorn does not automatically read ``nadzor-ai/.env``
in that mode, while several runtime limits are evaluated at module import time.
Load the project-local file before importing the rest of ``app`` so the same
configuration is used regardless of how the backend was started.

Explicit process environment variables always win over values from ``.env``.
Pytest intentionally skips the automatic load to keep regression tests isolated
from a developer machine's private configuration; tests may call
``load_project_env`` directly when needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _parse_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def load_project_env(path: str | Path | None = None) -> int:
    """Load simple KEY=VALUE entries without overriding the real environment."""
    env_path = Path(path) if path is not None else Path(__file__).resolve().parents[3] / ".env"
    if not env_path.is_file():
        return 0

    loaded = 0
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "A").isalnum() or key[0].isdigit():
            continue
        if key in os.environ:
            continue
        os.environ[key] = _parse_env_value(raw_value)
        loaded += 1
    return loaded


if "pytest" not in sys.modules:
    load_project_env()
