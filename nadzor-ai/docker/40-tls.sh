#!/bin/sh
# Включить HTTPS (TLS 1.3), если сертификат сервера смонтирован в /etc/nginx/certs.
# Без сертификата интерфейс работает по HTTP на 5173 — это видно в журнале.
set -eu
if [ -s /etc/nginx/certs/server.crt ] && [ -s /etc/nginx/certs/server.key ]; then
    cp /opt/nginx-tls.conf /etc/nginx/conf.d/tls.conf
    echo "40-tls.sh: HTTPS (TLS 1.3) включён на порту 5443"
else
    rm -f /etc/nginx/conf.d/tls.conf
    echo "40-tls.sh: сертификат не найден в /etc/nginx/certs — HTTPS выключен"
fi
