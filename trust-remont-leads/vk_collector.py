"""Сбор постов со стен VK-групп через официальный метод wall.search."""

import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger("vk_collector")

VK_API_URL = "https://api.vk.com/method/wall.search"
VK_API_VERSION = "5.199"


def _format_ts(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def search_group_wall(token, owner, query, count=100, timeout=15):
    """owner: screen_name группы (например 'zhk_primer') либо числовой owner_id."""
    params = {
        "access_token": token,
        "v": VK_API_VERSION,
        "query": query,
        "count": min(count, 100),
        "extended": 1,
    }
    owner_str = str(owner)
    if owner_str.lstrip("-").isdigit():
        params["owner_id"] = owner_str
    else:
        params["domain"] = owner_str

    try:
        resp = requests.get(VK_API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Сетевая ошибка VK для %s: %s", owner, exc)
        return []
    except ValueError as exc:
        logger.warning("VK вернул некорректный JSON для %s: %s", owner, exc)
        return []

    if "error" in data:
        err = data["error"]
        logger.warning(
            "Ошибка VK API для %s: код=%s сообщение=%s",
            owner,
            err.get("error_code"),
            err.get("error_msg"),
        )
        return []

    response = data.get("response", {})
    items = response.get("items", [])
    profiles = {p["id"]: p for p in response.get("profiles", [])}
    groups_info = {g["id"]: g for g in response.get("groups", [])}

    results = []
    for item in items:
        text = item.get("text", "")
        if not text:
            continue

        from_id = item.get("from_id")
        author = str(from_id) if from_id is not None else ""
        if from_id and from_id > 0 and from_id in profiles:
            p = profiles[from_id]
            author = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or author
        elif from_id and from_id < 0 and abs(from_id) in groups_info:
            author = groups_info[abs(from_id)].get("name", author)

        post_id = item.get("id")
        owner_id = item.get("owner_id")
        link = f"https://vk.com/wall{owner_id}_{post_id}" if owner_id and post_id else ""

        results.append(
            {
                "date": _format_ts(item.get("date")),
                "source": "VK",
                "group_name": owner_str,
                "author": author,
                "text": text,
                "link": link,
            }
        )

    return results


def collect_from_groups(token, groups, queries, pause_seconds=0.5):
    """Уважает лимиты VK API: пауза между запросами (VK допускает ~3 запроса/сек)."""
    all_results = []
    for group in groups:
        group = group.strip()
        if not group:
            continue
        for query in queries:
            all_results.extend(search_group_wall(token, group, query))
            time.sleep(pause_seconds)
    return all_results
