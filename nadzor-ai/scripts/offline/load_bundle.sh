#!/usr/bin/env bash
# Запуск на стенде БЕЗ Интернета: загрузить образы, сверить с манифестом,
# поднять систему. Повторный запуск безопасен.
#
# Требования к стенду: Linux, Docker с compose, NVIDIA-драйвер и
# NVIDIA Container Toolkit. Драйвер в комплект не входит.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

MANIFEST=bundle/MANIFEST.txt
[ -f "$MANIFEST" ] || { echo "нет $MANIFEST — сначала scripts/offline/prepare_bundle.sh" >&2; exit 1; }
value() { grep -m1 "^$1=" "$MANIFEST" | cut -d= -f2-; }

MODEL_DIR="$(value model_dir)"
[ -f "models/${MODEL_DIR}/config.json" ] || {
  echo "веса модели не найдены: models/${MODEL_DIR}" >&2; exit 1; }

echo "1/3 Загрузка образов"
docker load -i bundle/nadzor-images.tar

echo "2/3 Сверка образов с манифестом"
# Загружен ровно тот образ, что собран и проверен, а не одноимённый.
grep '^image_id ' "$MANIFEST" | while read -r _ pair; do
  image="${pair%%=*}"; expected="${pair#*=}"
  actual="$(docker image inspect -f '{{.Id}}' "$image")"
  [ "$actual" = "$expected" ] || { echo "образ $image не совпадает с манифестом" >&2; exit 1; }
done

echo "3/3 Запуск"
NADZOR_MODEL_DIR="$MODEL_DIR" docker compose up -d --no-build
echo "Интерфейс: http://localhost:5173  API: http://localhost:8010/docs"
echo "Первый запуск модели — несколько минут (загрузка весов в GPU): docker compose ps"
