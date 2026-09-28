#!/usr/bin/env bash
# Проверка запуска без видеокарты: дублёр модели + движок на временных базах
# + сквозной сценарий через /api/v1. Ничего не оставляет после себя.
#
#   make smoke            (или bash scripts/smoke/run_local.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
WORK="$(mktemp -d)"
MODEL_PORT="${SMOKE_MODEL_PORT:-18001}"
API_PORT="${SMOKE_API_PORT:-18010}"
# Учётная запись администратора временной базы: вход обязателен (ТЗ 12).
export NADZOR_ADMIN_LOGIN="${NADZOR_ADMIN_LOGIN:-admin}"
export NADZOR_ADMIN_PASSWORD="${NADZOR_ADMIN_PASSWORD:-smoke-admin-password}"
PIDS=()
cleanup() { for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done; rm -rf "$WORK"; }
trap cleanup EXIT

"$PY" "$ROOT/scripts/smoke/fake_model.py" --port "$MODEL_PORT" --model smoke-model &
PIDS+=($!)
(
  cd "$ROOT/packages/backend"
  export NADZOR_DB_PATH="$WORK/nadzor.db" FACTS_STORE_DB="$WORK/facts.db" \
         FILE_STORE_DB="$WORK/files.db" FILE_STORE_CACHE="$WORK/cache" \
         NADZOR_RUN_LOGS_DIR="$WORK/logs" NADZOR_LLM_CACHE_DB="$WORK/llm-cache.sqlite3" \
         NADZOR_LOCAL_LLM_URL="http://127.0.0.1:$MODEL_PORT" NADZOR_LOCAL_LLM_MODEL=smoke-model \
         PYTHONPATH=.
  exec "$PY" -m uvicorn app.main:app --port "$API_PORT" --log-level warning
) > "$WORK/backend.log" 2>&1 &
PIDS+=($!)

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$API_PORT/health" >/dev/null && break
  sleep 1
done
"$PY" "$ROOT/scripts/smoke/e2e_api.py" --base-url "http://127.0.0.1:$API_PORT" \
  || { echo "--- журнал движка ---"; tail -40 "$WORK/backend.log"; exit 1; }
