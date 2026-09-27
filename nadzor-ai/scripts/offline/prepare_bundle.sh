#!/usr/bin/env bash
# Сборка офлайн-комплекта. Выполняется ОДИН раз на машине с Интернетом.
#
# Результат — всё, что нужно стенду без сети:
#   bundle/nadzor-images.tar   образы backend, frontend и сервера модели
#   models/<каталог модели>    веса модели (с диска, не из сети)
#   bundle/MANIFEST.txt        версии: ревизия весов, ID образов, SHA-256
#
# Перенос на стенд: каталог репозитория вместе с bundle/ и models/.
# Запуск на стенде: scripts/offline/load_bundle.sh (без Интернета).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

VLLM_IMAGE="vllm/vllm-openai:v0.28.0"
VLLM_DIGEST="sha256:61fc8a896b0a4fbbbdc063bc4b0dbc25ce98e02b5050c24aeb7830ac02039b14"
MODEL_REPO="${NADZOR_MODEL_REPO:-Qwen/Qwen2.5-VL-7B-Instruct}"
MODEL_DIR="${NADZOR_MODEL_DIR:-Qwen2.5-VL-7B-Instruct}"
# Ревизия весов фиксируется коммитом репозитория модели. Пусто — берётся
# текущая, и её коммит записывается в манифест: воспроизводимость
# обеспечивает запись, а не угадывание.
MODEL_REVISION="${NADZOR_MODEL_REVISION:-}"

mkdir -p bundle models

echo "1/4 Образ сервера модели по дайджесту"
docker pull "vllm/vllm-openai@${VLLM_DIGEST}"
docker tag "vllm/vllm-openai@${VLLM_DIGEST}" "$VLLM_IMAGE"

echo "2/4 Веса модели ${MODEL_REPO}"
# Скачивание идёт инструментом из того же образа vLLM: на машине ничего
# дополнительно ставить не нужно.
docker run --rm \
  -v "$ROOT/models:/models" \
  -e MODEL_REPO="$MODEL_REPO" -e MODEL_DIR="$MODEL_DIR" -e MODEL_REVISION="$MODEL_REVISION" \
  --entrypoint python3 "$VLLM_IMAGE" -c '
import os
from huggingface_hub import HfApi, snapshot_download
repo, rev = os.environ["MODEL_REPO"], os.environ["MODEL_REVISION"] or None
sha = HfApi().model_info(repo, revision=rev).sha
snapshot_download(repo_id=repo, revision=sha, local_dir="/models/" + os.environ["MODEL_DIR"])
open("/models/" + os.environ["MODEL_DIR"] + "/REVISION", "w").write(sha + "\n")
print("ревизия весов:", sha)
'

echo "3/4 Образы backend и frontend"
docker compose build backend frontend

echo "4/4 Сохранение образов и манифест"
docker save -o bundle/nadzor-images.tar \
  "$VLLM_IMAGE" nadzor-ai/backend:local nadzor-ai/frontend:local
{
  echo "# Офлайн-комплект НАДЗОР.ИИ — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "model_repo=${MODEL_REPO}"
  echo "model_revision=$(cat "models/${MODEL_DIR}/REVISION")"
  echo "model_dir=${MODEL_DIR}"
  echo "vllm_image=${VLLM_IMAGE}"
  echo "vllm_digest=${VLLM_DIGEST}"
  for image in "$VLLM_IMAGE" nadzor-ai/backend:local nadzor-ai/frontend:local; do
    echo "image_id ${image}=$(docker image inspect -f '{{.Id}}' "$image")"
  done
  echo "images_tar_sha256=$(sha256sum bundle/nadzor-images.tar | cut -d' ' -f1)"
  echo "# SHA-256 файлов весов"
  (cd "models/${MODEL_DIR}" && find . -type f ! -name REVISION -print0 | sort -z | xargs -0 sha256sum)
} > bundle/MANIFEST.txt

echo "Готово: bundle/ и models/${MODEL_DIR}. Перенесите их на стенд вместе с репозиторием."
