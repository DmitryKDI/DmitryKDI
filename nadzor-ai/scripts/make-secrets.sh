#!/usr/bin/env bash
# Секреты контура — один раз при развёртывании. В git не идут (.gitignore).
#
#   secrets/db.key          ключ шифрования базы (SQLCipher, ТЗ 12, п.3)
#   secrets/storage.key     ключ шифрования хранилища оригиналов (AES-256-GCM)
#   secrets/cache.key       ключ шифрования кэша ML в Redis
#   secrets/internal.token  служебный токен между сервером и ML-модулями
#   .env                    пароли RabbitMQ и Redis (дописываются, если их нет)
#
# Существующие файлы не перезаписываются: потеря ключа базы и хранилища —
# потеря данных. Резервная копия ключей — забота администратора.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p secrets

make_key() {
  if [ -s "secrets/$1" ]; then
    echo "secrets/$1 уже есть — не трогаю"
  else
    openssl rand -hex 32 > "secrets/$1"
    echo "secrets/$1 создан"
  fi
}
for name in db.key storage.key cache.key internal.token; do make_key "$name"; done

touch .env
for name in INSPECTOR_AMQP_PASSWORD INSPECTOR_REDIS_PASSWORD; do
  if grep -q "^${name}=" .env; then
    echo "${name} уже задан в .env"
  else
    echo "${name}=$(openssl rand -hex 24)" >> .env
    echo "${name} добавлен в .env"
  fi
done
# Файлы секретов читает пользователь приложения в контейнере (uid 10001).
chmod 0644 secrets/*
