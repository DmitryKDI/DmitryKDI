"""Сбор сообщений из публичных Telegram-групп/чатов через Telethon.

Читает ТОЛЬКО публичные группы, в которые вошёл пользователь аккаунта,
использованного для авторизации Telethon-сессии. Никакого обхода приватности.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import timezone

logger = logging.getLogger("trust_remont.telegram")


def _parse_groups(raw: str) -> list[str]:
    return [g.strip() for g in raw.split(",") if g.strip()]


def collect(conn: sqlite3.Connection) -> list[dict]:
    """Возвращает список новых сообщений из настроенных Telegram-групп.

    conn — открытое соединение SQLite, используется для хранения last_message_id
    по каждой группе, чтобы не сканировать одни и те же сообщения повторно.
    """
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        logger.warning("Telethon не установлен — пропускаю сбор из Telegram")
        return []

    import storage

    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    groups_raw = os.environ.get("TELEGRAM_GROUPS", "")
    session_name = os.environ.get("TELEGRAM_SESSION_NAME", "trust_remont_session")
    limit = int(os.environ.get("TELEGRAM_MESSAGE_LIMIT", "200"))

    groups = _parse_groups(groups_raw)
    if not api_id or not api_hash:
        logger.warning("TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы — пропускаю Telegram")
        return []
    if not groups:
        logger.warning("TELEGRAM_GROUPS пуст — нечего собирать в Telegram")
        return []

    messages: list[dict] = []
    with TelegramClient(session_name, int(api_id), api_hash) as client:
        for group in groups:
            group_key = group.lstrip("@").rstrip("/").split("/")[-1]
            last_id_raw = storage.get_state(conn, "telegram", group_key)
            min_id = int(last_id_raw) if last_id_raw else 0

            try:
                entity = client.get_entity(group)
                group_title = getattr(entity, "title", group_key)
            except Exception as exc:
                logger.error("Не удалось получить группу %s: %s", group, exc)
                continue

            newest_id = min_id
            try:
                for msg in client.iter_messages(entity, limit=limit, min_id=min_id):
                    if not msg.text:
                        continue
                    newest_id = max(newest_id, msg.id)

                    sender = msg.sender
                    if sender:
                        author = (
                            getattr(sender, "username", None)
                            or getattr(sender, "first_name", None)
                            or str(msg.sender_id)
                        )
                    else:
                        author = str(msg.sender_id)

                    link = f"https://t.me/{group_key}/{msg.id}"
                    messages.append(
                        {
                            "date": msg.date.astimezone(timezone.utc).isoformat(),
                            "source": "Telegram",
                            "group_name": group_title,
                            "author": author,
                            "text": msg.text,
                            "link": link,
                        }
                    )
            except Exception as exc:
                logger.error("Ошибка при чтении группы %s: %s", group, exc)
                continue

            if newest_id > min_id:
                storage.set_state(conn, "telegram", group_key, str(newest_id))

    logger.info("Telegram: собрано %d новых сообщений из %d групп", len(messages), len(groups))
    return messages
