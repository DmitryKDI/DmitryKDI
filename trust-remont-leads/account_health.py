"""Проверка состояния уже настроенного Telegram/VK-аккаунта перед сбором.

Это НЕ пул из нескольких аккаунтов и не ротация — только диагностика
одного аккаунта, который уже указан в .env: жива ли сессия, не отозван
ли токен, не под лимитом ли он сейчас. Ничего секретного в лог не пишется.
"""

import logging

import requests
from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError

logger = logging.getLogger("account_health")

VK_API_VERSION = "5.199"


async def check_telegram(session_name, api_id, api_hash):
    """Проверяет состояние уже существующей Telegram-сессии, не запрашивая новый вход.

    Возвращает {"status": "ok"|"expired"|"revoked"|"rate_limited"|"error", "detail": str}.
    """
    client = TelegramClient(session_name, api_id, api_hash)
    try:
        await client.connect()
    except Exception as exc:
        logger.warning("Telegram: не удалось подключиться (%s)", exc.__class__.__name__)
        return {"status": "error", "detail": exc.__class__.__name__}

    try:
        if not await client.is_user_authorized():
            logger.warning("Telegram: сессия не авторизована или истекла")
            return {"status": "expired", "detail": "сессия не авторизована"}

        me = await client.get_me()
        username = getattr(me, "username", None) or "без юзернейма"
        logger.info("Telegram: аккаунт активен (%s, id=%s)", username, me.id)
        return {"status": "ok", "detail": username}
    except AuthKeyUnregisteredError:
        logger.warning("Telegram: ключ авторизации отозван (аккаунт разлогинен или заблокирован)")
        return {"status": "revoked", "detail": "auth key unregistered"}
    except FloodWaitError as exc:
        logger.warning("Telegram: FloodWait %s секунд — аккаунт временно ограничен", exc.seconds)
        return {"status": "rate_limited", "detail": f"flood wait {exc.seconds}s"}
    except Exception as exc:
        logger.warning("Telegram: ошибка проверки состояния (%s)", exc)
        return {"status": "error", "detail": str(exc)}
    finally:
        try:
            await client.disconnect()
        except Exception as exc:
            logger.debug("Telegram: ошибка при отключении после health-check (%s)", exc)


def check_vk(token, timeout=10):
    """Проверяет, что VK-токен действителен и не находится под ограничением частоты.

    Возвращает {"status": "ok"|"invalid"|"rate_limited"|"error", "detail": str}.
    """
    params = {"access_token": token, "v": VK_API_VERSION}
    try:
        resp = requests.get("https://api.vk.com/method/users.get", params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("VK: сетевая ошибка при проверке токена (%s)", exc)
        return {"status": "error", "detail": "сетевая ошибка"}
    except ValueError as exc:
        logger.warning("VK: некорректный ответ при проверке токена (%s)", exc)
        return {"status": "error", "detail": "некорректный JSON"}

    if "error" in data:
        code = data["error"].get("error_code")
        if code == 5:
            logger.warning("VK: токен недействителен или отозван")
            return {"status": "invalid", "detail": "invalid token"}
        if code in (6, 29):
            logger.warning("VK: превышен лимит запросов (код %s)", code)
            return {"status": "rate_limited", "detail": f"error_code={code}"}
        logger.warning("VK: ошибка API при проверке токена (код %s)", code)
        return {"status": "error", "detail": f"error_code={code}"}

    user = (data.get("response") or [{}])[0]
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
    logger.info("VK: токен действителен (%s)", name or "аккаунт")
    return {"status": "ok", "detail": name}


def format_report(tg_result, vk_result):
    lines = ["=== Состояние аккаунтов ==="]
    if tg_result is None:
        lines.append("Telegram: не настроен (нет TG_API_ID/TG_API_HASH)")
    else:
        lines.append(f"Telegram: {tg_result['status']} ({tg_result['detail']})")
    if vk_result is None:
        lines.append("VK: не настроен (нет VK_TOKEN)")
    else:
        lines.append(f"VK: {vk_result['status']} ({vk_result['detail']})")
    return "\n".join(lines)


async def _main():
    import os

    from dotenv import load_dotenv

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    tg_result = None
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    if api_id and api_hash:
        session_name = os.getenv("TG_SESSION_NAME", "trust_remont_session")
        tg_result = await check_telegram(session_name, int(api_id), api_hash)

    vk_result = None
    vk_token = os.getenv("VK_TOKEN")
    if vk_token:
        vk_result = check_vk(vk_token)

    print(format_report(tg_result, vk_result))


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
