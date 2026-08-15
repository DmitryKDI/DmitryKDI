"""Сбор постов со стен пабликов ВКонтакте через официальный API (wall.search).

Используются только официальные методы VK API с валидным access_token —
никакого скрапинга HTML-страниц ВК и обхода ограничений доступа.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone

import requests

import filter as lead_filter

logger = logging.getLogger("trust_remont.vk")

VK_API_URL = "https://api.vk.com/method/wall.search"
REQUEST_DELAY_SECONDS = 0.4  # держим запас от лимита ~3 запроса/сек


def _parse_groups(raw: str) -> list[str]:
    return [g.strip() for g in raw.split(",") if g.strip()]


def _call_api(params: dict, retries: int = 3) -> dict:
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(VK_API_URL, params=params, timeout=15)
            data = response.json()
        except requests.RequestException as exc:
            logger.error("Сетевая ошибка VK API: %s", exc)
            return {}

        if "error" in data:
            error_code = data["error"].get("error_code")
            if error_code == 6:  # too many requests per second
                wait = 1.0 * attempt
                logger.warning("VK API rate limit, жду %.1fs и повторяю", wait)
                time.sleep(wait)
                continue
            logger.error("VK API ошибка: %s", data["error"].get("error_msg"))
            return {}
        return data.get("response", {})
    return {}


def collect(conn: sqlite3.Connection) -> list[dict]:
    token = os.environ.get("VK_ACCESS_TOKEN")
    groups_raw = os.environ.get("VK_GROUPS", "")
    api_version = os.environ.get("VK_API_VERSION", "5.199")
    count = int(os.environ.get("VK_SEARCH_COUNT", "50"))

    groups = _parse_groups(groups_raw)
    if not token:
        logger.warning("VK_ACCESS_TOKEN не задан — пропускаю VK")
        return []
    if not groups:
        logger.warning("VK_GROUPS пуст — нечего собирать в VK")
        return []

    trigger_phrases = lead_filter.load_keywords().get("trigger_phrases", [])
    messages: list[dict] = []
    seen_post_ids: set[str] = set()

    for domain in groups:
        for phrase in trigger_phrases:
            params = {
                "domain": domain,
                "query": phrase,
                "count": count,
                "owners_only": 0,
                "access_token": token,
                "v": api_version,
            }
            data = _call_api(params)
            time.sleep(REQUEST_DELAY_SECONDS)

            for item in data.get("items", []):
                post_id = f"{item.get('owner_id')}_{item.get('id')}"
                if post_id in seen_post_ids:
                    continue
                seen_post_ids.add(post_id)

                text = item.get("text", "")
                if not text:
                    continue

                author_id = item.get("from_id") or item.get("owner_id")
                link = f"https://vk.com/wall{item.get('owner_id')}_{item.get('id')}"
                post_date = datetime.fromtimestamp(item.get("date", 0), tz=timezone.utc)
                messages.append(
                    {
                        "date": post_date.isoformat(),
                        "source": "VK",
                        "group_name": domain,
                        "author": str(author_id),
                        "text": text,
                        "link": link,
                    }
                )

    logger.info("VK: собрано %d уникальных постов из %d групп", len(messages), len(groups))
    return messages
