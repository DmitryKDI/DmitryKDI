"""ТРАСТ РЕМОНТ — точка входа: запускает все сборщики, фильтрует и сохраняет тёплые лиды.

Разовый запуск:  python main.py                (RUN_MODE=once, по умолчанию)
Фоновый цикл:    RUN_MODE=loop в .env, затем python main.py
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import config
import filter as lead_filter
import forum_collector
import storage
import telegram_collector
import vk_collector

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

COLLECTORS = [telegram_collector, vk_collector, forum_collector]

logger = logging.getLogger("trust_remont.main")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "run.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def run_once(conn, keywords: dict, csv_path: str, xlsx_path: str) -> int:
    new_leads: list[dict] = []
    total_seen = 0
    total_filtered_out = 0

    for collector in COLLECTORS:
        try:
            raw_messages = collector.collect(conn)
        except Exception:
            logger.exception("Сборщик %s упал с ошибкой, пропускаю его", collector.__name__)
            continue

        for msg in raw_messages:
            total_seen += 1
            verdict = lead_filter.evaluate_message(msg["text"], keywords)
            if not verdict.is_lead:
                total_filtered_out += 1
                logger.debug(
                    "Отфильтровано (%s, %s): %s", msg["source"], verdict.reason, msg["text"][:80]
                )
                continue

            lead = {
                "date": msg["date"],
                "source": msg["source"],
                "group_name": msg["group_name"],
                "author": msg["author"],
                "text": msg["text"],
                "link": msg["link"],
                "keywords": ", ".join(verdict.matched_triggers),
                "confidence": verdict.confidence,
                "status": "новый",
            }
            if storage.save_lead(conn, lead):
                new_leads.append(lead)
            else:
                logger.debug("Дубликат лида, пропускаю: %s", msg["text"][:80])

    if new_leads:
        storage.append_to_csv(csv_path, new_leads)
        storage.append_to_xlsx(xlsx_path, new_leads)

    logger.info(
        "Итог прогона: сообщений просмотрено %d, отфильтровано %d, новых лидов %d",
        total_seen,
        total_filtered_out,
        len(new_leads),
    )
    return len(new_leads)


def main() -> None:
    config.load_env()
    setup_logging()

    db_path = config.get_env("DB_PATH", "data/leads.db")
    csv_path = config.get_env("CSV_PATH", "output/leads.csv")
    xlsx_path = config.get_env("XLSX_PATH", "output/leads.xlsx")
    run_mode = config.get_env("RUN_MODE", "once").lower()
    interval_minutes = float(config.get_env("RUN_INTERVAL_MINUTES", "30"))

    keywords = lead_filter.load_keywords()
    conn = storage.init_db(db_path)

    logger.info("Запуск ТРАСТ РЕМОНТ Lead Finder (режим: %s)", run_mode)

    try:
        if run_mode == "loop":
            while True:
                run_once(conn, keywords, csv_path, xlsx_path)
                logger.info("Пауза %.0f минут до следующего прогона", interval_minutes)
                time.sleep(interval_minutes * 60)
        else:
            run_once(conn, keywords, csv_path, xlsx_path)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
