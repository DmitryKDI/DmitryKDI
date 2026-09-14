#!/usr/bin/env python3
"""Пошаговая проверка связи с провайдером: где именно рвётся.

Сообщение «ConnectTimeout» ничего не говорит о причине: не отвечает
сервер авторизации, закрыт нестандартный порт, не разрешается имя, режет
антивирус или прокси — действия во всех случаях разные. Скрипт проходит
цепочку по шагам и печатает, какой из них не прошёл.

Запуск:
    python scripts/check_gigachat.py
"""
from __future__ import annotations

import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app import llm  # noqa: E402


def step(title: str) -> None:
    print(f"\n— {title}")


def check_host(url: str) -> bool:
    """Имя → адрес → TCP-соединение → рукопожатие TLS, по шагам."""
    parsed = urlparse(url)
    host = parsed.hostname or url
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    step(f"{host}:{port}")

    try:
        started = time.monotonic()
        addresses = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        print(f"   имя разрешается: {addresses[0][4][0]} "
              f"({(time.monotonic() - started) * 1000:.0f} мс)")
    except Exception as exc:  # noqa: BLE001 — печатаем причину как есть
        print(f"   ИМЯ НЕ РАЗРЕШАЕТСЯ: {exc}")
        print("   Это DNS: проверьте сеть и настройки резолвера "
              "(в WSL — /etc/resolv.conf).")
        return False

    try:
        started = time.monotonic()
        with socket.create_connection((host, port), timeout=15) as raw:
            print(f"   TCP-соединение есть ({(time.monotonic() - started) * 1000:.0f} мс)")
            context = ssl.create_default_context()
            bundle = llm.ca_bundle()
            if isinstance(bundle, str):
                context.load_verify_locations(cafile=bundle)
            elif bundle is False:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            with context.wrap_socket(raw, server_hostname=host) as tls:
                cert = tls.getpeercert()
                issuer = dict(x[0] for x in cert.get("issuer", ())).get("organizationName", "?")
                print(f"   TLS в порядке, сертификат выдан: {issuer}")
    except ssl.SSLError as exc:
        print(f"   TLS НЕ ПРОШЁЛ: {exc}")
        print(f"   Сейчас используется: {llm.ca_bundle_description()}")
        print("   Положите корневой сертификат в каталог certs/ и перезапустите.")
        return False
    except (TimeoutError, socket.timeout):
        print("   ТАЙМАУТ СОЕДИНЕНИЯ")
        print(f"   Порт {port} не отвечает. Чаще всего его закрывает межсетевой "
              "экран, антивирус или корпоративная сеть — особенно нестандартный "
              "порт вроде 9443. Проверьте на другой сети (например, мобильной).")
        return False
    except Exception as exc:  # noqa: BLE001 — печатаем причину как есть
        print(f"   СОЕДИНЕНИЕ НЕ УСТАНОВЛЕНО: {type(exc).__name__}: {exc}")
        return False
    return True


def main() -> None:
    print("Проверка связи с провайдером по шагам")
    print(f"Сертификаты: {llm.ca_bundle_description()}")

    key = llm.credentials_from_file("gigachat")
    source = "файл в secrets/"
    if not key:
        import os
        key = os.environ.get("GIGACHAT_CREDENTIALS", "")
        source = "переменная окружения"
    print(f"Ключ: {'найден (' + source + ')' if key else 'НЕ НАЙДЕН'}")

    hosts_ok = all([
        check_host(llm.GIGACHAT_OAUTH_URL),
        check_host(llm.GIGACHAT_API_BASE),
    ])

    if not key:
        print("\nБез ключа дальше проверять нечего: положите файл из личного "
              "кабинета в каталог secrets/.")
        raise SystemExit(2)
    if not hosts_ok:
        print("\nДо хостов провайдера не достучаться — вызов делать бессмысленно, "
              "причина выше.")
        raise SystemExit(2)

    step("рабочий вызов")
    config = llm.LlmConfig(provider="gigachat", api_key=key, model="")
    ok, why = llm.check_llm_reachable(config)
    print(f"   {'СВЯЗЬ ЕСТЬ' if ok else 'НЕ ПРОШЛО'}: {why}")
    raise SystemExit(0 if ok else 2)


if __name__ == "__main__":
    main()
