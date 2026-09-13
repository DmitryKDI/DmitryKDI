"""Поиск релевантных форумов/страниц в интернете по ключевым словам.

Важно: здесь используется официальный поисковый API (Google Programmable
Search Engine / Custom Search JSON API), а НЕ скрейпинг страницы выдачи
google.com или yandex.ru. Прямой скрейпинг HTML выдачи поисковика:
  1) прямо запрещён условиями использования Google/Яндекса;
  2) почти гарантированно приводит к блокировке IP или капче в первые же
     минуты — то есть именно то, чего вы просили избежать.
Официальный API с ключом — единственный способ делать программный поиск
по интернету, не рискуя блокировкой на этом шаге. У Google Custom Search
JSON API есть бесплатный лимит 100 запросов/день — этого достаточно для
периодической выборки новых страниц.

Найденные URL передаются в forum_collector.py, который уже сам проверяет
robots.txt перед скачиванием каждой страницы.
"""

import logging
import time

import requests

logger = logging.getLogger("web_discovery")

GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"


def search_web(api_key, cx, query, num=10, timeout=15):
    """Возвращает список URL из результатов поиска по одному запросу."""
    params = {"key": api_key, "cx": cx, "q": query, "num": min(num, 10)}
    try:
        resp = requests.get(GOOGLE_CSE_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Ошибка запроса поиска для '%s': %s", query, exc)
        return []
    except ValueError as exc:
        logger.warning("Некорректный JSON от поискового API для '%s': %s", query, exc)
        return []

    if "error" in data:
        logger.warning(
            "Поисковый API вернул ошибку для '%s': %s",
            query,
            data["error"].get("message", data["error"]),
        )
        return []

    return [item["link"] for item in data.get("items", []) if item.get("link")]


def discover_urls(api_key, cx, queries, max_urls=20, pause_seconds=1.0):
    """Ищет по всем запросам, дедуплицирует URL, ограничивает итоговое число.

    Ограничение по числу URL — не только про экономию квоты API, но и про то,
    чтобы за один прогон не обстреливать разом десятки новых сайтов запросами
    (см. README, раздел про снижение риска блокировки).
    """
    seen = set()
    urls = []
    for query in queries:
        for url in search_web(api_key, cx, query):
            if url in seen:
                continue
            seen.add(url)
            urls.append(url)
            if len(urls) >= max_urls:
                return urls
        time.sleep(pause_seconds)
    return urls
