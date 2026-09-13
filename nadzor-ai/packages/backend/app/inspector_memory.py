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
    db.commit()
    return db


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
    with _connect(path) as db:
        row = db.execute(
            "SELECT id FROM lessons WHERE lesson = ? AND enabled = 1",
            (text,),
        ).fetchone()
        if row:
            return int(row[0])
        cur = db.execute(
            "INSERT INTO lessons(lesson, source_type, source_id, created_at) VALUES(?,?,?,?)",
            (text, str(source_type or "manual"), str(source_id or ""), time.time()),
        )
        db.commit()
        return int(cur.lastrowid)


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
