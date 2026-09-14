"""Local experience memory for the stateful investigator.

Lessons are local machine data (SQLite, ignored by git). Blind benchmark runs
never read learned lessons, so evaluator feedback cannot leak back into a
supposedly blind rerun. Experienced/demo mode may use generalized lessons.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

from .llm import LlmConfig, call_llm_json

ROOT = Path(__file__).resolve().parents[3]
MEMORY_PATH = Path(
    os.environ.get("NADZOR_INSPECTOR_MEMORY")
    or ROOT / "data" / "inspector_memory.sqlite3"
)

_SPECIFIC_LESSON_PATTERNS = (
    re.compile(r"(?i)\b[^\s]+\.pdf\b"),
    re.compile(r"(?i)\b(?:лист|страниц[аеуы]?|page|sheet)\s*[№#:]?\s*\d+\b"),
    re.compile(r"(?i)\b(?:пом(?:ещение|\.)?|room)\s*[№#:]?\s*\d+\b"),
    re.compile(r"[A-Za-z]:\\"),
)


def _generic_lesson(text: str) -> bool:
    """Reject obvious object-specific identifiers from cross-run memory."""
    return not any(pattern.search(text) for pattern in _SPECIFIC_LESSON_PATTERNS)


def blind_mode() -> bool:
    return os.environ.get("NADZOR_BLIND_BENCHMARK", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _connect(path: Path = MEMORY_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30.0)
    db.execute(
        """CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS playbook (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            body TEXT NOT NULL DEFAULT '',
            lesson_count INTEGER NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL
        )"""
    )
    db.commit()
    return db


def refresh_master_playbook(
    *,
    path: Path = MEMORY_PATH,
    max_chars: int = 12000,
) -> str:
    """Rebuild a compact persistent playbook from all enabled generic lessons.

    This first version is intentionally deterministic: exact/normalized duplicates
    are collapsed and the remaining generic lessons are kept in learning order.
    Later versions may replace this with semantic consolidation without changing
    the runtime contract.
    """
    budget = max(1000, min(30000, int(max_chars)))
    with _connect(path) as db:
        rows = db.execute(
            "SELECT lesson FROM lessons WHERE enabled = 1 ORDER BY id ASC"
        ).fetchall()
        seen: set[str] = set()
        items: list[str] = []
        used = 0
        for row in rows:
            lesson = " ".join(str(row[0] or "").split()).strip()
            key = lesson.casefold()
            if not lesson or key in seen or not _generic_lesson(lesson):
                continue
            line = f"- {lesson}"
            extra = len(line) + (1 if items else 0)
            if items and used + extra > budget:
                break
            if not items and len(line) > budget:
                line = line[:budget].rstrip()
                lesson = line[2:].strip()
            seen.add(key)
            items.append(lesson)
            used += extra
        body = "\n".join(f"- {item}" for item in items)
        db.execute(
            """INSERT INTO playbook(id, body, lesson_count, updated_at)
               VALUES(1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 body = excluded.body,
                 lesson_count = excluded.lesson_count,
                 updated_at = excluded.updated_at""",
            (body, len(items), time.time()),
        )
        db.commit()
    return body


def master_playbook(*, path: Path = MEMORY_PATH) -> str:
    """Return the persistent generic playbook, hidden in blind mode."""
    if blind_mode() or not path.exists():
        return ""
    with _connect(path) as db:
        row = db.execute("SELECT body FROM playbook WHERE id = 1").fetchone()
    if row and str(row[0] or "").strip():
        return str(row[0])
    return refresh_master_playbook(path=path)


def add_lesson(
    lesson: str,
    *,
    source_type: str = "manual",
    source_id: str = "",
    path: Path = MEMORY_PATH,
) -> int:
    text = " ".join(str(lesson or "").split()).strip()
    if not text:
        raise ValueError("lesson is empty")
    if len(text) > 1200:
        text = text[:1200].rstrip()
    if not _generic_lesson(text):
        raise ValueError("lesson contains object-specific identifiers")
    created = False
    with _connect(path) as db:
        row = db.execute(
            "SELECT id FROM lessons WHERE lesson = ? AND enabled = 1",
            (text,),
        ).fetchone()
        if row:
            lesson_id = int(row[0])
        else:
            cur = db.execute(
                "INSERT INTO lessons(lesson, source_type, source_id, created_at) VALUES(?,?,?,?)",
                (text, str(source_type or "manual"), str(source_id or ""), time.time()),
            )
            db.commit()
            lesson_id = int(cur.lastrowid)
            created = True
    if created:
        refresh_master_playbook(path=path)
    return lesson_id


def active_lessons(*, limit: int = 24, path: Path = MEMORY_PATH) -> list[dict]:
    """Return generalized lessons for experienced mode.

    Blind evaluation deliberately sees an empty list regardless of source.
    """
    if blind_mode():
        return []
    if not path.exists():
        return []
    with _connect(path) as db:
        rows = db.execute(
            """SELECT id, lesson, source_type, source_id, created_at
               FROM lessons WHERE enabled = 1
               ORDER BY id DESC LIMIT ?""",
            (max(1, min(100, int(limit))),),
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "lesson": str(row[1]),
            "source_type": str(row[2]),
            "source_id": str(row[3]),
            "created_at": float(row[4]),
        }
        for row in reversed(rows)
    ]


def lessons_prompt(*, limit: int = 24, path: Path = MEMORY_PATH) -> str:
    """Return the long-term inspector experience injected into each new analysis."""
    if blind_mode():
        return ""
    playbook = master_playbook(path=path)
    if playbook:
        return (
            "\nMASTER PLAYBOOK ИНСПЕКТОРА. Это накопленные ОБЩИЕ правила прошлых "
            "проверок, а не подсказки о текущем комплекте. Не считай их "
            "доказательством; используй как стратегию поиска и перепроверки:\n"
            + playbook
            + "\n"
        )
    lessons = active_lessons(limit=limit, path=path)
    if not lessons:
        return ""
    body = "\n".join(f"- {row['lesson']}" for row in lessons)
    return (
        "\nОПЫТ ПРЕДЫДУЩИХ ПРОВЕРОК. Это общие правила, а не подсказки о текущем "
        "комплекте. Не считай их доказательством; используй только как стратегию "
        "поиска и перепроверки:\n" + body + "\n"
    )


_REFLECT_SYSTEM = """Ты превращаешь обратную связь о промахе строительного AI-инспектора
в ОБЩЕЕ правило работы для будущих, неизвестных комплектов документов.
Нельзя сохранять названия файлов, номера листов, помещений, конкретные марки,
ожидаемые benchmark-находки или любой ground truth текущего комплекта.
Нужна стратегия: что перепроверять, когда не завершать поиск, какой evidence
запрашивать, как не спутать отсутствие и ненаблюдаемость. Верни только JSON:
{"lessons":["короткое обобщённое правило", "..."]}.
Если обратная связь слишком конкретная и не даёт общего правила — lessons=[].
"""


def learn_from_feedback(
    config: LlmConfig,
    feedback: str,
    *,
    source_type: str = "teacher",
    source_id: str = "",
    path: Path = MEMORY_PATH,
) -> list[int]:
    """Generalize teacher feedback and store it for experienced/demo mode.

    Even lessons learned from benchmark feedback stay disabled in blind mode
    because :func:`active_lessons` returns nothing there.
    """
    text = str(feedback or "").strip()
    if not text:
        return []
    result = call_llm_json(
        config,
        _REFLECT_SYSTEM,
        "ОБРАТНАЯ СВЯЗЬ:\n" + text[:12000],
        operation="text_verify",
        source_digest="inspector-feedback",
        prompt_version="inspector-reflection-v1",
        use_cache=False,
    )
    lessons = result.get("lessons") if isinstance(result, dict) else []
    ids: list[int] = []
    for item in lessons if isinstance(lessons, list) else []:
        lesson = " ".join(str(item or "").split()).strip()
        if lesson and _generic_lesson(lesson):
            ids.append(
                add_lesson(
                    lesson,
                    source_type=source_type,
                    source_id=source_id,
                    path=path,
                )
            )
    return ids
