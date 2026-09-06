#!/usr/bin/env bash
# Установка всего необходимого для запуска на домашнем ПК. Без Docker и без
# PostgreSQL: оба сервера работают на файловой базе SQLite.
#
# Скрипт идемпотентный — повторный запуск ничего не переделывает, поэтому его
# безопасно вызывать при каждом старте (см. start-all.sh). Долгие шаги
# отмечаются файлами-метками с хэшем исходного списка зависимостей: список
# изменился — шаг повторяется, не изменился — пропускается.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
# shellcheck source=lib.sh
. scripts/lib.sh

STAMPS="$ROOT/.setup-stamps"
mkdir -p "$STAMPS"

# Метка «шаг выполнен для такого-то состояния входных файлов».
stamp_current() {  # stamp_current <имя> <файл-источник...>
  local name="$1"; shift
  local now; now="$(cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1)"
  [ -f "$STAMPS/$name" ] && [ "$(cat "$STAMPS/$name")" = "$now" ]
}
stamp_write() {
  local name="$1"; shift
  cat "$@" 2>/dev/null | sha256sum | cut -d' ' -f1 > "$STAMPS/$name"
}

say "Проверка окружения"

case "$ROOT" in
  /mnt/[a-z]/*)
    warn "проект лежит на диске Windows ($ROOT)."
    warn "Установка там идёт в десятки раз медленнее и часто обрывается."
    warn "Перенесите проект в домашнюю папку Linux, например в ~/nadzor-project."
    ;;
esac

# Недостающее собираем целиком и показываем одной командой: получать «поставьте
# X», а после установки — «поставьте Y», и так несколько раз подряд, хуже, чем
# один раз увидеть весь список.
MISSING=""

command -v git >/dev/null 2>&1 || MISSING="$MISSING git"

if command -v python3 >/dev/null 2>&1; then
  # venv в Debian/Ubuntu ставится отдельным пакетом, и без него установка
  # обрывается на середине с невнятной ошибкой. Проверяем заранее.
  python3 -c 'import ensurepip' >/dev/null 2>&1 || MISSING="$MISSING python3-venv python3-pip"
else
  MISSING="$MISSING python3 python3-venv python3-pip"
fi

if [ ! -x /usr/bin/node ] && ! command -v node >/dev/null 2>&1; then
  MISSING="$MISSING nodejs npm"
fi

if [ -n "$MISSING" ]; then
  printf '\n%s\n' "${RED}${BOLD}Не хватает системных пакетов:${MISSING}${OFF}" >&2
  printf '%s\n' "Выполните одну команду и запустите снова:" >&2
  printf '\n  %s\n\n' "${BOLD}sudo apt update && sudo apt install -y${MISSING}${OFF}" >&2
  exit 1
fi
ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)"

NPM="$(pick_npm)"; NODE="$(pick_node)"
ok "node $("$NODE" -v)"

say "1/4 Библиотеки Python"
if stamp_current python requirements.txt; then
  ok "уже установлены"
else
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
  stamp_write python requirements.txt
  ok "установлены"
fi

say "2/4 Библиотеки интерфейса"
# node_modules мог быть удалён вручную — проверяем и метку, и саму папку.
if stamp_current npm frontend/package.json && [ -d frontend/node_modules ]; then
  ok "уже установлены"
else
  (cd frontend && "$NPM" install --no-audit --no-fund --loglevel=error)
  stamp_write npm frontend/package.json
  ok "установлены"
fi

say "3/4 Демонстрационный комплект документации"
if [ -f data/demo/generated/manifest.json ]; then
  ok "уже сформирован"
else
  PYTHONPATH=. ./.venv/bin/python scripts/gen_dataset.py >/dev/null
  ok "сформирован"
fi

# Г.71 — провайдер ЛЛМ сокращён до Anthropic/GigaChat (app/llm.py), локальной
# модели (Ollama) в бэкенде больше нет вообще — раньше здесь проверялась
# установка Ollama и наличие модели qwen2.5vl, но provider="local" теперь
# даёт ValueError: unknown provider при первом же вызове. Ключ настраивается
# в интерфейсе (Новый анализ -> Настроить ИИ), не через локальную модель.
say "4/4 Провайдер ЛЛМ"
ok "Anthropic/GigaChat настраиваются в интерфейсе (Новый анализ -> Настроить ИИ)"

printf '\n%s\n' "${GREEN}${BOLD}Готово к запуску.${OFF}"
