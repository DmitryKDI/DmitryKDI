#!/usr/bin/env bash
# Прогон сверки требований (Г.32/Г.33/Г.36) с ОБЩИМ ЛЛМ-извлечением из
# прозы ПД (requirement_llm_extract.py — работает на прозе любого формата,
# не завязано на один документ) и эскалацией кандидатов без кода в зрение
# по листу РД (vision_page_compare.py). В отличие от run_no_llm.sh —
# ТРЕБУЕТ ключ провайдера (по умолчанию GigaChat), читает его из отдельного
# файла ключей, а не из общего .env приложения.
# Вызывается из RUN-VISION.bat (Windows) через WSL, либо напрямую:
#   bash scripts/run_vision_requirements.sh /путь/ПД /путь/РД /путь/vision-keys.env
set -euo pipefail
cd "$(dirname "$0")/.."

BEFORE_DIR="${1:?использование: run_vision_requirements.sh ПАПКА_ПД ПАПКА_РД ФАЙЛ_КЛЮЧЕЙ}"
AFTER_DIR="${2:?использование: run_vision_requirements.sh ПАПКА_ПД ПАПКА_РД ФАЙЛ_КЛЮЧЕЙ}"
KEYS_ENV="${3:?использование: run_vision_requirements.sh ПАПКА_ПД ПАПКА_РД ФАЙЛ_КЛЮЧЕЙ}"

if [ ! -f "$KEYS_ENV" ]; then
  echo "Файл ключей не найден: $KEYS_ENV" >&2
  exit 1
fi

# set -a — переменные из файла ключей попадают в окружение процесса python
# (тот же приём, что scripts/start-all.sh для общего .env), поэтому
# registry_diff.py находит ключ сам по имени переменной провайдера
# (GIGACHAT_CREDENTIALS), без передачи ключа литералом в аргументе команды.
set -a
# shellcheck disable=SC1090
. "$KEYS_ENV"
set +a

if [ -z "${GIGACHAT_CREDENTIALS:-}" ]; then
  echo "$KEYS_ENV найден, но GIGACHAT_CREDENTIALS в нём пуст — заполните ключ и запустите снова" >&2
  exit 1
fi

before_args=()
for f in "$BEFORE_DIR"/*.pdf; do
  [ -f "$f" ] && before_args+=(--before "$f")
done
after_args=()
for f in "$AFTER_DIR"/*.pdf; do
  [ -f "$f" ] && after_args+=(--after "$f")
done

if [ ${#before_args[@]} -eq 0 ]; then
  echo "В папке ПД ($BEFORE_DIR) нет ни одного .pdf" >&2
  exit 1
fi
if [ ${#after_args[@]} -eq 0 ]; then
  echo "В папке РД ($AFTER_DIR) нет ни одного .pdf" >&2
  exit 1
fi

PY=./.venv/bin/python3
[ -x "$PY" ] || PY=python3

echo "ПД: ${#before_args[@]} файл(ов), РД: ${#after_args[@]} файл(ов)"
echo

"$PY" scripts/registry_diff.py "${before_args[@]}" "${after_args[@]}" \
  --kind requirements --verify-requirements --provider gigachat
