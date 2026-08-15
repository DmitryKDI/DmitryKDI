"""SQLite-дедупликация и запись тёплых лидов в CSV и XLSX."""
from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("trust_remont.storage")

CSV_HEADERS = [
    "Дата", "Источник", "Название группы", "Автор", "Текст сообщения",
    "Ссылка", "Совпавшие ключевые слова", "Уверенность (0-1)", "Статус",
]


def compute_hash(text: str, author: str) -> str:
    normalized = f"{(author or '').strip().lower()}::{(text or '').strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def init_db(db_path: str | Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            hash TEXT PRIMARY KEY,
            date TEXT,
            source TEXT,
            group_name TEXT,
            author TEXT,
            text TEXT,
            link TEXT,
            keywords TEXT,
            confidence REAL,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            source TEXT,
            group_key TEXT,
            last_value TEXT,
            PRIMARY KEY (source, group_key)
        )
        """
    )
    conn.commit()
    return conn


def is_duplicate(conn: sqlite3.Connection, lead_hash: str) -> bool:
    row = conn.execute("SELECT 1 FROM leads WHERE hash = ?", (lead_hash,)).fetchone()
    return row is not None


def save_lead(conn: sqlite3.Connection, lead: dict) -> bool:
    """Сохраняет лид в SQLite. Возвращает False, если это дубликат (текст+автор)."""
    lead_hash = compute_hash(lead["text"], lead["author"])
    if is_duplicate(conn, lead_hash):
        return False
    conn.execute(
        """
        INSERT INTO leads (hash, date, source, group_name, author, text, link, keywords, confidence, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_hash,
            lead["date"],
            lead["source"],
            lead["group_name"],
            lead["author"],
            lead["text"],
            lead["link"],
            lead["keywords"],
            lead["confidence"],
            lead.get("status", "новый"),
        ),
    )
    conn.commit()
    return True


def get_state(conn: sqlite3.Connection, source: str, group_key: str) -> str | None:
    row = conn.execute(
        "SELECT last_value FROM state WHERE source = ? AND group_key = ?",
        (source, group_key),
    ).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, source: str, group_key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO state (source, group_key, last_value) VALUES (?, ?, ?)
        ON CONFLICT(source, group_key) DO UPDATE SET last_value = excluded.last_value
        """,
        (source, group_key, value),
    )
    conn.commit()


def _lead_row(lead: dict) -> list:
    return [
        lead["date"],
        lead["source"],
        lead["group_name"],
        lead["author"],
        lead["text"],
        lead["link"],
        lead["keywords"],
        lead["confidence"],
        lead.get("status", "новый"),
    ]


def append_to_csv(csv_path: str | Path, leads: list[dict]) -> None:
    """Дописывает новые лиды в конец CSV (создаёт файл с шапкой, если его ещё нет)."""
    if not leads:
        return
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    is_new_file = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(CSV_HEADERS)
        for lead in leads:
            writer.writerow(_lead_row(lead))


def append_to_xlsx(xlsx_path: str | Path, leads: list[dict]) -> bool:
    """Дописывает новые лиды в XLSX с жирной шапкой и автошириной колонок.

    Если openpyxl не установлен, пропускает запись и возвращает False —
    выполнение скрипта не должно прерываться из-за отсутствия необязательной зависимости.
    """
    if not leads:
        return True
    try:
        import openpyxl
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        logger.warning(
            "openpyxl не установлен — пропускаю запись в XLSX, CSV всё равно обновлён"
        )
        return False

    xlsx_path = Path(xlsx_path)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)

    if xlsx_path.exists():
        workbook = openpyxl.load_workbook(xlsx_path)
        sheet = workbook.active
    else:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Лиды"
        sheet.append(CSV_HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True)

    for lead in leads:
        sheet.append(_lead_row(lead))

    for col_idx, header in enumerate(CSV_HEADERS, start=1):
        column_letter = get_column_letter(col_idx)
        max_len = len(str(header))
        for cell in sheet[column_letter]:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        sheet.column_dimensions[column_letter].width = min(max_len + 2, 80)

    workbook.save(xlsx_path)
    return True
