#!/usr/bin/env bash
# Запуск всей системы одной командой. Поднимает три процесса:
#
#   8000  packages/api      — сайт целиком: дашборд, журналы, объекты, аудит
#   8010  packages/backend  — сравнение загруженных документов (Anthropic/GigaChat)
#   5173  frontend          — интерфейс, проксирует оба порта (vite.config.ts)
#
# Оба сервера работают на SQLite, поэтому ни Docker, ни PostgreSQL не нужны.
# Закрытие окна или Ctrl+C останавливает всё разом.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
# shellcheck source=lib.sh
. scripts/lib.sh

LOGS="$ROOT/logs"; mkdir -p "$LOGS"
PIDS=()

cleanup() {
  printf '\n%s\n' "${BOLD}Останавливаю...${OFF}"
  for pid in "${PIDS[@]:-}"; do
    [ -n "${pid:-}" ] || continue
    pkill -P "$pid" 2>/dev/null || true   # дочерние процессы uvicorn/vite
    kill "$pid" 2>/dev/null || true
  done
  printf '%s\n' "Остановлено."
}
trap cleanup EXIT INT TERM

./scripts/setup.sh

# Г.71 — провайдер ЛЛМ сокращён до Anthropic/GigaChat, ключ вводится в
# интерфейсе (Новый анализ -> Настроить ИИ) и хранится в БД (Settings), не
# читается из .env. Файл .env, если есть, всё ещё загружается — он может
# нести GIGACHAT_API_BASE/GIGACHAT_SCOPE/GIGACHAT_CA_BUNDLE (см. app/llm.py),
# но сам API-ключ туда не идёт.
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  ok ".env загружен"
fi

say "Освобождаю порты"
# Процессы прошлого запуска могли остаться, если окно закрыли жёстко. Бьём
# точечно по своим командам, чтобы не задеть чужое на этих портах.
pkill -f 'uvicorn api.main:app'   2>/dev/null || true
pkill -f 'uvicorn app.main:app'   2>/dev/null || true
pkill -f 'node_modules/.bin/vite' 2>/dev/null || true
sleep 1
for p in 8000 8010 5173; do
  port_busy "$p" && die "порт $p занят посторонней программой. Закройте её и запустите снова."
done
ok "8000, 8010, 5173 свободны"

say "Запускаю серверы"

# Сайт целиком. APP_ROOT нужен, чтобы сервер нашёл config/ и data/ при любом
# текущем каталоге; PYTHONPATH — чтобы пакеты импортировались как `api.*`.
# --reload следит только за packages/ (исходники), не за всем деревом:
# наблюдение за корнем зацепило бы каждую запись в data/ и .venv/, вызывая
# перезапуск на любой загрузке документа. packages/backend исключён отдельно
# — у него свои uploads/ и nadzor.db, за которые следит уже второй процесс.
PYTHONPATH="$ROOT/packages" APP_ROOT="$ROOT" \
  ./.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 \
  --reload --reload-dir "$ROOT/packages" --reload-exclude "packages/backend/*" \
  >"$LOGS/site.log" 2>&1 &
PIDS+=($!)

# Сравнение документов. Зависимости — подмножество общего requirements.txt тех
# же версий, поэтому окружение Python одно на оба сервера, а не два.
# reload-dir сужен до app/ — соседние uploads/ и nadzor.db меняются на
# каждую загрузку файла и не должны перезапускать сервер посреди анализа.
(cd packages/backend && PYTHONPATH="$ROOT/packages/backend" \
  "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 \
  --reload --reload-dir "$ROOT/packages/backend/app") \
  >"$LOGS/compare.log" 2>&1 &
PIDS+=($!)

# Первый запуск сайта разбирает демонстрационный комплект и наполняет базу —
# это заметно дольше обычного старта, отсюда запас по времени.
printf '  сайт (первый запуск наполняет базу, до полутора минут)... '
wait_for_port 8000 site 150 || { printf '\n'; cat "$LOGS/site.log" | tail -25; die "сайт не поднялся"; }
printf '%s\n' "${GREEN}готов${OFF}"

printf '  сравнение документов... '
wait_for_port 8010 compare 60 || { printf '\n'; cat "$LOGS/compare.log" | tail -25; die "сервер сравнения не поднялся"; }
printf '%s\n' "${GREEN}готов${OFF}"

NPM="$(pick_npm)"
(cd frontend && "$NPM" run dev) >"$LOGS/web.log" 2>&1 &
PIDS+=($!)

printf '  интерфейс... '
wait_for_port 5173 web 90 || { printf '\n'; cat "$LOGS/web.log" | tail -25; die "интерфейс не поднялся"; }
printf '%s\n' "${GREEN}готов${OFF}"

cat <<EOF

${GREEN}${BOLD}Всё работает.${OFF}

  Откройте:  ${BOLD}http://localhost:5173${OFF}

  На странице входа выберите любую учётную запись — роли отличаются тем,
  что видно на экранах. «Новый анализ» — загрузка своих файлов и сравнение.

  Это окно закрывать нельзя: пока оно открыто, система работает.
  Остановить — Ctrl+C или просто закрыть окно.

EOF

# Браузер открываем сами и только сейчас: раньше он показал бы ошибку
# соединения там, где всё в порядке, — сервер просто ещё не встал.
#
# Метка времени в адресе — не для сервера (главная страница её игнорирует),
# а для браузера: у Chrome/Edge `start URL` с адресом, который уже открыт в
# другой вкладке, просто переключается на неё вместо новой загрузки — и
# показывает страницу такой, какой она была при прошлом запуске батника,
# даже если сервер за это время перезапустился с новым кодом. Уникальный
# адрес на каждый запуск гарантирует свежую вкладку и настоящую загрузку.
if command -v cmd.exe >/dev/null 2>&1; then
  (cd /mnt/c 2>/dev/null && cmd.exe /c start "" "http://localhost:5173/?started=$(date +%s)" >/dev/null 2>&1) || true
fi

wait
