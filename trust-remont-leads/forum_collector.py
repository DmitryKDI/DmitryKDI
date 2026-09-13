"""Сбор публичных сообщений с форумов/Q&A-страниц через requests + BeautifulSoup."""

import logging
import time
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("forum_collector")

USER_AGENT = "Mozilla/5.0 (compatible; TrustRemontLeadBot/1.0; +informational)"


def _allowed_by_robots(url):
    try:
        parsed = requests.utils.urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # Если сам robots.txt недоступен — не блокируем сбор из-за инфраструктурной ошибки.
        return True


def fetch_forum_page(url, selector=None, timeout=15):
    if not _allowed_by_robots(url):
        logger.warning("Пропускаю %s: запрещено robots.txt", url)
        return []

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Не удалось загрузить страницу форума %s: %s", url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    title_tag = soup.find("title")
    group_name = title_tag.get_text(strip=True) if title_tag else url

    nodes = soup.select(selector) if selector else soup.find_all(["p", "div"], limit=500)

    results = []
    seen_texts = set()
    for node in nodes:
        text = node.get_text(" ", strip=True)
        if not text or len(text) < 15 or text in seen_texts:
            continue
        seen_texts.add(text)
        results.append(
            {
                "date": "",
                "source": "Форум",
                "group_name": group_name,
                "author": "",
                "text": text,
                "link": url,
            }
        )
    return results


def collect_from_forums(urls, selector=None, pause_seconds=1.0):
    all_results = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        all_results.extend(fetch_forum_page(url, selector=selector))
        time.sleep(pause_seconds)
    return all_results
