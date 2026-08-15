"""Точка входа: запускает все сборщики лидов по очереди, разово или в цикле."""

import asyncio
import logging
import os
import time

from dotenv import load_dotenv

import filter as filter_module
import forum_collector
import storage
import telegram_collector
import vk_collector

load_dotenv()

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, "run.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


def _split_env_list(name, default=""):
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def process_batch(raw_messages, lead_filter, conn):
    saved, skipped = 0, 0
    for msg in raw_messages:
        is_lead, matched, confidence = lead_filter.classify(msg.get("text", ""))
        if not is_lead:
            skipped += 1
            continue
        msg["matched_keywords"] = matched
        msg["confidence"] = confidence
        msg["status"] = "новый"
        if storage.save_lead(conn, msg):
            saved += 1
        else:
            skipped += 1  # дубликат
    return saved, skipped


async def run_telegram(lead_filter, conn):
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    session_name = os.getenv("TG_SESSION_NAME", "trust_remont_session")
    chats = _split_env_list("TG_MONITOR_CHATS")
    lookback_hours = int(os.getenv("TG_LOOKBACK_HOURS", "24"))

    if not api_id or not api_hash:
        logger.info("Telegram отключён: не заданы TG_API_ID/TG_API_HASH")
        return
    if not chats:
        logger.info("Telegram: список TG_MONITOR_CHATS пуст, пропускаю")
        return

    client = telegram_collector.build_client(session_name, int(api_id), api_hash)
    try:
        await client.start()
        messages = await telegram_collector.collect_messages(
            client, chats, lookback_hours=lookback_hours
        )
        saved, skipped = process_batch(messages, lead_filter, conn)
        logger.info(
            "Telegram: получено %d сообщений, новых лидов %d, отфильтровано %d",
            len(messages),
            saved,
            skipped,
        )
    except Exception as exc:
        logger.error("Ошибка в Telegram-сборщике: %s", exc)
    finally:
        try:
            await client.disconnect()
        except Exception as exc:
            logger.debug("Ошибка при отключении Telegram-клиента: %s", exc)


def run_vk(lead_filter, conn):
    token = os.getenv("VK_TOKEN")
    groups = _split_env_list("VK_GROUPS")
    queries = _split_env_list("VK_SEARCH_QUERIES") or ["ремонт квартиры"]

    if not token:
        logger.info("VK отключён: не задан VK_TOKEN")
        return
    if not groups:
        logger.info("VK: список VK_GROUPS пуст, пропускаю")
        return

    try:
        messages = vk_collector.collect_from_groups(token, groups, queries)
        saved, skipped = process_batch(messages, lead_filter, conn)
        logger.info(
            "VK: получено %d сообщений, новых лидов %d, отфильтровано %d",
            len(messages),
            saved,
            skipped,
        )
    except Exception as exc:
        logger.error("Ошибка в VK-сборщике: %s", exc)


def run_forums(lead_filter, conn):
    urls = _split_env_list("FORUM_URLS")
    selector = os.getenv("FORUM_SELECTOR") or None

    if not urls:
        logger.info("Форумы: список FORUM_URLS пуст, пропускаю")
        return

    try:
        messages = forum_collector.collect_from_forums(urls, selector=selector)
        saved, skipped = process_batch(messages, lead_filter, conn)
        logger.info(
            "Форумы: получено %d сообщений, новых лидов %d, отфильтровано %d",
            len(messages),
            saved,
            skipped,
        )
    except Exception as exc:
        logger.error("Ошибка в форум-сборщике: %s", exc)


async def run_once():
    lead_filter = filter_module.LeadFilter(os.getenv("KEYWORDS_PATH", "keywords.json"))
    conn = storage.init_db(os.getenv("DB_PATH", "leads.db"))
    try:
        await run_telegram(lead_filter, conn)
        run_vk(lead_filter, conn)
        run_forums(lead_filter, conn)
    finally:
        conn.close()


def main():
    loop_mode = os.getenv("LOOP_MODE", "false").lower() in ("1", "true", "yes")
    interval_minutes = int(os.getenv("LOOP_INTERVAL_MINUTES", "30"))

    if not loop_mode:
        asyncio.run(run_once())
        return

    logger.info("Запуск в режиме цикла: каждые %d минут", interval_minutes)
    while True:
        try:
            asyncio.run(run_once())
        except Exception as exc:
            logger.error("Необработанная ошибка цикла: %s", exc)
        time.sleep(interval_minutes * 60)


if __name__ == "__main__":
    main()
