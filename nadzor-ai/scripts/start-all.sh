#!/usr/bin/env bash
# Запуск системы одной командой. Поднимает два процесса:
#
#   8010  packages/backend  — движок разбора документов (Anthropic/GigaChat)
#   5173  frontend          — интерфейс (vite.config.ts проксирует на 8010)
#
# Г.85: старый сервер на порту 8000 (packages/api — дашборд, журналы, аудит)
# удалён вместе со всей CRM-витриной; остался один движок.
# Работает на SQLite, поэтому ни Docker, ни PostgreSQL не нужны.
# Закрытие окна или Ctrl+C останавливает всё разом.
# Некоторые установки WSL запускают урезанный Bash без опции pipefail.
# Для запуска сервисов достаточно строгих режимов errexit и nounset.
set -eu
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
pkill -f 'uvicorn app.main:app'   2>/dev/null || true
pkill -f 'node_modules/.bin/vite' 2>/dev/null || true
sleep 1
for p in 8010 5173; do
  port_busy "$p" && die "порт $p занят посторонней программой. Закройте её и запустите снова."
done
ok "8010, 5173 свободны"

say "Очищаю рабочее место прошлого запуска"
# Новый запуск программы всегда начинается с чистого комплекта документов и
# пустых результатов. Настройки ИИ/API-ключ не трогаются: clear_workspace()
# сохраняет таблицу settings. Blob-хранилище тоже остаётся кэшем и наружу не
# показывается — повторная загрузка того же PDF не обязана заново копировать
# байты на диск.
(cd packages/backend && PYTHONPATH="$ROOT/packages/backend" \
  "$ROOT/.venv/bin/python" -c \
  'from app.db import init_db, clear_workspace; init_db(); deleted = clear_workspace(); print("workspace cleared:", sum(deleted.values()), "rows")') \
  >"$LOGS/clean-start.log" 2>&1 || { cat "$LOGS/clean-start.log"; die "не удалось очистить рабочее место"; }
ok "документы и прошлые прогоны очищены; настройки ИИ сохранены"

say "Запускаю серверы"

# Сравнение документов. Зависимости — подмножество общего requirements.txt тех
# же версий, поэтому окружение Python одно на оба сервера, а не два.
# reload-dir сужен до app/ — соседние uploads/ и nadzor.db меняются на
# каждую загрузку файла и не должны перезапускать сервер посреди анализа.
(cd packages/backend && PYTHONPATH="$ROOT/packages/backend" \
  "$ROOT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 \
  --reload --reload-dir "$ROOT/packages/backend/app") \
  >"$LOGS/compare.log" 2>&1 &
PIDS+=($!)

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
# Фронтенд дополнительно использует наличие started= как сигнал сбросить
# только сохранённое состояние прошлой сессии в localStorage.
if command -v cmd.exe >/dev/null 2>&1; then
  (cd /mnt/c 2>/dev/null && cmd.exe /c start "" "http://localhost:5173/?started=$(date +%s)" >/dev/null 2>&1) || true
fi

wait
