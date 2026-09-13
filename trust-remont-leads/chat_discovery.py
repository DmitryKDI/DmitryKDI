"""Автопоиск публичных Telegram-групп/каналов по ключевым словам.

Отдельный шаг перед постоянным мониторингом: результаты сохраняются
в discovered_chats.csv, чтобы список можно было проверить руками
перед добавлением чатов в TG_MONITOR_CHATS.
"""

import asyncio
import csv
import logging
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.types import Channel, Chat

logger = logging.getLogger("chat_discovery")

DISCOVERED_CSV_COLUMNS = ["Название", "Юзернейм", "Ссылка", "Участников", "Ключевое слово"]


def _split_list(raw):
    return [item.strip() for item in raw.split(",") if item.strip()]


async def discover_chats(client, keywords, limit_per_keyword=20, pause_seconds=1.0):
    found = {}
    for kw in keywords:
        try:
            result = await client(SearchRequest(q=kw, limit=limit_per_keyword))
        except FloodWaitError as exc:
            logger.warning("Flood wait %ss при поиске '%s', жду", exc.seconds, kw)
            await asyncio.sleep(exc.seconds)
            continue
        except Exception as exc:
            logger.warning("Ошибка поиска по ключевому слову '%s': %s", kw, exc)
            continue

        for chat in getattr(result, "chats", []):
            if isinstance(chat, (Channel, Chat)) and getattr(chat, "username", None):
                found[chat.id] = {
                    "title": getattr(chat, "title", "") or "",
                    "username": chat.username,
                    "link": f"https://t.me/{chat.username}",
                    "participants_count": getattr(chat, "participants_count", "") or "",
                    "matched_keyword": kw,
                }

        await asyncio.sleep(pause_seconds)

    return list(found.values())


def save_discovered_chats(chats, csv_path="discovered_chats.csv"):
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(DISCOVERED_CSV_COLUMNS)
        for chat in chats:
            writer.writerow(
                [
                    chat.get("title", ""),
                    chat.get("username", ""),
                    chat.get("link", ""),
                    chat.get("participants_count", ""),
                    chat.get("matched_keyword", ""),
                ]
            )


async def _main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    load_dotenv()

    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    session_name = os.getenv("TG_SESSION_NAME", "trust_remont_session")
    keywords = _split_list(os.getenv("DISCOVERY_KEYWORDS", "")) or [
        "ремонт квартир Москва",
        "новостройка ремонт",
    ]

    if not api_id or not api_hash:
        print("Не заданы TG_API_ID / TG_API_HASH в .env")
        return

    client = TelegramClient(session_name, int(api_id), api_hash)
    await client.start()
    try:
        chats = await discover_chats(client, keywords)
        save_discovered_chats(chats)
        print(f"Найдено {len(chats)} чатов, сохранено в discovered_chats.csv")
        print("Проверьте список и вручную добавьте нужные юзернеймы в TG_MONITOR_CHATS в .env")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
