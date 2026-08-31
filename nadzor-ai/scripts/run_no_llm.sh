#!/usr/bin/env bash
# Прогон реестрового diff'а (помещения + оборудование, Приложение Г.9/Г.20)
# БЕЗ ЛЛМ — чистый код, никакого API-ключа не требует. Вызывается из
# RUN-NO-LLM.bat (Windows) через WSL, либо напрямую:
#   bash scripts/run_no_llm.sh /путь/к/папке/ПД /путь/к/папке/РД
set -euo pipefail
cd "$(dirname "$0")/.."

BEFORE_DIR="${1:?использование: run_no_llm.sh ПАПКА_ПД ПАПКА_РД}"
AFTER_DIR="${2:?использование: run_no_llm.sh ПАПКА_ПД ПАПКА_РД}"

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

"$PY" scripts/registry_diff.py "${before_args[@]}" "${after_args[@]}" --kind both
