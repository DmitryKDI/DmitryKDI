"""Сбор сообщений из публичных Telegram-групп/каналов через Telethon."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, FloodWaitError, UsernameNotOccupiedError

logger = logging.getLogger("telegram_collector")


def build_client(session_name, api_id, api_hash):
    return TelegramClient(session_name, api_id, api_hash)


async def _resolve_entity(client, chat):
    try:
        return await client.get_entity(chat)
    except FloodWaitError as exc:
        logger.warning("Flood wait %ss при получении чата %s, жду", exc.seconds, chat)
        await asyncio.sleep(exc.seconds)
    except (UsernameNotOccupiedError, ChannelPrivateError, ValueError) as exc:
        logger.warning("Чат недоступен, пропускаю: %s (%s)", chat, exc.__class__.__name__)
    except Exception as exc:
        logger.warning("Неожиданная ошибка при получении чата %s: %s", chat, exc)
    return None


async def collect_messages(client, chats, lookback_hours=24, per_chat_limit=200):
    """Возвращает список сырых сообщений (без фильтрации по ключевым словам)."""
    results = []
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    for raw_chat in chats:
        chat = raw_chat.strip()
        if not chat:
            continue

        entity = await _resolve_entity(client, chat)
        if entity is None:
            continue

        group_name = getattr(entity, "title", None) or getattr(entity, "username", None) or chat
        username = getattr(entity, "username", None)

        try:
            async for message in client.iter_messages(entity, limit=per_chat_limit):
                if message.date and message.date < since:
                    break  # сообщения идут от новых к старым, дальше можно не читать
                if not message.text:
                    continue

                author = str(message.sender_id) if message.sender_id else "unknown"
                if message.sender is not None:
                    author = (
                        getattr(message.sender, "username", None)
                        or getattr(message.sender, "first_name", None)
                        or author
                    )

                link = (
                    f"https://t.me/{username}/{message.id}"
                    if username
                    else f"internal://{entity.id}/{message.id}"
                )

                results.append(
                    {
                        "date": message.date.isoformat() if message.date else "",
                        "source": "Telegram",
                        "group_name": group_name,
                        "author": author,
                        "text": message.text,
                        "link": link,
                    }
                )
                await asyncio.sleep(0.05)  # мягкий темп запросов
        except FloodWaitError as exc:
            logger.warning("Flood wait %ss при чтении %s, жду", exc.seconds, chat)
            await asyncio.sleep(exc.seconds)
        except Exception as exc:
            logger.warning("Ошибка при чтении сообщений из %s: %s", chat, exc)
            continue

    return results
