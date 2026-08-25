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

command -v python3 >/dev/null 2>&1 || die "не найден python3. Установите: sudo apt install -y python3 python3-venv python3-pip"
# venv в Debian/Ubuntu ставится отдельным пакетом, и без него ошибка приходит
# уже в середине установки, в невнятном виде. Проверяем заранее.
python3 -c 'import ensurepip' >/dev/null 2>&1 \
  || die "не хватает python3-venv. Установите: sudo apt install -y python3-venv python3-pip"
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

say "4/4 Локальная модель Ollama"
if command -v ollama >/dev/null 2>&1; then
  if ollama list 2>/dev/null | grep -q 'qwen2.5vl'; then
    ok "qwen2.5vl на месте"
  else
    warn "модель qwen2.5vl:7b не найдена."
    warn "Сайт и все экраны будут работать, но сравнение чертежей — нет."
    warn "Скачать: ollama pull qwen2.5vl:7b"
  fi
else
  warn "Ollama не установлена — сайт работать будет, ИИ-сравнение чертежей нет."
  warn "Установить: curl -fsSL https://ollama.com/install.sh | sh"
fi

printf '\n%s\n' "${GREEN}${BOLD}Готово к запуску.${OFF}"
