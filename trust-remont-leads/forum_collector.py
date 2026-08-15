"""Сбор сообщений с публичных форумов/Q&A-разделов про ремонт квартир.

Источники и CSS-селекторы настраиваются в forum_sources.json без изменения кода.
Скачиваются только уже опубликованные публичные HTML-страницы.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

logger = logging.getLogger("trust_remont.forum")

SOURCES_PATH = Path(__file__).parent / "forum_sources.json"
REQUEST_TIMEOUT = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TrustRemontLeadFinder/1.0)"}


def _load_sources() -> list[dict]:
    if not SOURCES_PATH.exists():
        return []
    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources = json.load(f)
    return [s for s in sources if s.get("enabled", True)]


def collect(conn: sqlite3.Connection) -> list[dict]:
    if os.environ.get("FORUM_ENABLED", "true").lower() not in ("1", "true", "yes"):
        logger.info("FORUM_ENABLED=false — пропускаю сбор с форумов")
        return []

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("requests/beautifulsoup4 не установлены — пропускаю сбор с форумов")
        return []

    sources = _load_sources()
    if not sources:
        logger.warning(
            "forum_sources.json пуст или все источники отключены — нечего собирать с форумов"
        )
        return []

    messages: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for source in sources:
        name = source.get("name", source.get("url"))
        url = source.get("url")
        message_selector = source.get("message_selector")
        author_selector = source.get("author_selector")
        link_selector = source.get("link_selector")
        if not url or not message_selector:
            logger.warning("Пропускаю источник форума без url/message_selector: %s", source)
            continue

        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Не удалось загрузить %s: %s", url, exc)
            continue

        # response.content (байты) вместо response.text: requests угадывает кодировку
        # только по заголовку Content-Type и по умолчанию уходит в ISO-8859-1, если сервер
        # не указал charset явно — bs4 сам определит кодировку по <meta charset> и байтам.
        soup = BeautifulSoup(response.content, "html.parser")
        posts = soup.select(message_selector)
        for post in posts:
            text = post.get_text(" ", strip=True)
            if not text:
                continue

            author = "неизвестно"
            if author_selector:
                author_el = post.select_one(author_selector)
                if author_el:
                    author = author_el.get_text(" ", strip=True)

            link = url
            if link_selector:
                link_el = post.select_one(link_selector)
                if link_el and link_el.get("href"):
                    link = urljoin(url, link_el["href"])

            messages.append(
                {
                    "date": now,
                    "source": "Форум",
                    "group_name": name,
                    "author": author,
                    "text": text,
                    "link": link,
                }
            )

    logger.info("Форумы: собрано %d сообщений из %d источников", len(messages), len(sources))
    return messages
