#!/usr/bin/env bash
# Запуск бэкенда на локальном ПК. Один процесс, SQLite-файл рядом
# (packages/backend/nadzor.db), без Docker.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
