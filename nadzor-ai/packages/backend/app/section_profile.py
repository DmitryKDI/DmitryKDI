"""Накапливаемый профиль раздела документации (Г.105).

Указание пользователя: «код должен быть обобщённым, а конкретика и примеры по
проектам — уже в ходе эксплуатации программы; она будет пополняться, чтобы
быстрее работала и находила эти недочёты. Должна быть база данных с типизацией
разделов документации, а сама исполняемость программы — общей, чтобы она не
выискивала конкретный раздел из примеров, а просто шла по порядку по каждому
тому независимо и там уже определяла, какой это том».

## Что здесь есть

Механизм БЕЗ единого знания о каком-либо разделе. Он умеет три вещи:
запомнить наблюдение, посчитать, в скольких РАЗНЫХ томах оно встречалось, и
отдать накопленное обратно — подсказкой в промпт и строкой в отчёт. Какие
именно термины, нормативы и таблицы бывают в разделе, здесь не написано и
написано быть не может: это заполняется прогонами.

## Почему база, а не константа в коде

Константа в коде — это знание, добытое на одном комплекте и застывшее. Она
не растёт, её нельзя пополнить, не трогая код, и она немедленно превращает
механику в «программу про тот раздел, который видели». База растёт сама:
разобрали том — профиль его раздела пополнился, следующий том того же
раздела разбирается с подсказкой, накопленной на предыдущих.

## Честность накопленного

**Единица счёта — ТОМ, а не вхождение.** Термин, встреченный сто раз в одном
томе, — это по-прежнему n=1: одно наблюдение одного автора. Поэтому у каждой
записи хранится число РАЗНЫХ документов (`documents`), и подсказка говорит,
по скольким томам она накоплена. Повторный разбор того же файла счётчик не
увеличивает — иначе «уверенность» росла бы от перезапусков.

**Имя документа не хранится.** Для идемпотентности достаточно отпечатка
(`sha256` от имени), а имя тома — реквизит объекта, которому в хранимых
данных не место (Г.12/раздел 0 п.8).

**Пустой профиль — видимое состояние.** «По этому разделу данных пока нет»
и «в этом разделе ничего такого не бывает» — разные вещи, и первая обязана
называться словами, а не отсутствием строки (Г.10).

## Чем подсказка НЕ является

Накопленное не подставляется в результат и ничего не подтверждает. Это
подсказка вида «в томах этого раздела такое встречалось» — модель по-прежнему
обязана найти факт на странице. Иначе профиль превратился бы в генератор
правдоподобных находок, которых в документе нет (раздел 0, п.7).
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Integer, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# Отдельный файл, как и хранилище разборов: это накопленные данные, а не
# состояние приложения — их нельзя терять при пересоздании основной базы.
STORE_PATH = os.environ.get(
    "SECTION_PROFILE_DB",
    str(Path(__file__).resolve().parents[3] / "data" / "section_profiles.db"))

# Виды наблюдений. Список открыт по смыслу, но закрыт по коду: новый вид —
# это новый способ смотреть на документ, а не новая строка в данных.
KIND_NORM = "norm"      # обозначение норматива из перечня тома
KIND_TERM = "term"      # устойчивый оборот/термин, повторяющийся в требованиях
KIND_TABLE = "table"    # тип таблицы, реально встреченный в томе

UNKNOWN_SECTION = ""    # раздел не определён — тоже профиль, отдельный


class ProfileBase(DeclarativeBase):
    pass


class Observation(ProfileBase):
    __tablename__ = "section_observations"
    __table_args__ = (UniqueConstraint("section", "kind", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section: Mapped[str] = mapped_column(String, default="", index=True)
    kind: Mapped[str] = mapped_column(String, default="", index=True)
    value: Mapped[str] = mapped_column(String, default="")      # ключ сравнения
    display: Mapped[str] = mapped_column(String, default="")    # как показывать
    documents: Mapped[int] = mapped_column(Integer, default=0)  # честное n
    occurrences: Mapped[int] = mapped_column(Integer, default=0)


class Sighting(ProfileBase):
    """Отпечаток документа, в котором наблюдение встречено.

    Нужен ровно для одного: не считать один и тот же том дважды. Имя тома
    не хранится — только отпечаток (Г.12).
    """
    __tablename__ = "section_sightings"
    __table_args__ = (UniqueConstraint("observation_id", "document_digest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observation_id: Mapped[int] = mapped_column(Integer, index=True)
    document_digest: Mapped[str] = mapped_column(String, default="")


_engine = None
_SessionLocal = None
_engine_path: str | None = None


def _session() -> Session:
    """Ленивое подключение, переоткрывающееся при смене пути.

    Путь сравнивается с тем, на котором построено текущее подключение: иначе
    смена `STORE_PATH` (другая установка, отдельный профиль на прогон) молча
    продолжала бы писать в прежний файл — то есть данные уходили бы не туда,
    куда их направили, и молча.
    """
    global _engine, _SessionLocal, _engine_path
    if _SessionLocal is None or _engine_path != STORE_PATH:
        Path(STORE_PATH).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{STORE_PATH}",
                                connect_args={"check_same_thread": False})
        ProfileBase.metadata.create_all(bind=_engine)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
        _engine_path = STORE_PATH
    return _SessionLocal()


def _digest(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()[:32]


@dataclass
class ProfileEntry:
    kind: str
    value: str
    display: str
    documents: int
    occurrences: int


def observe(section: str | None, kind: str,
            values: Iterable[tuple[str, str] | str],
            document: str) -> int:
    """Запомнить наблюдения одного тома. Возвращает число новых записей.

    `values` — пары «ключ сравнения, как показывать» либо просто строки.
    `document` — имя тома: наружу не выходит и не хранится, используется
    только для отпечатка (см. докстринг модуля).
    """
    section = (section or UNKNOWN_SECTION).strip()
    digest = _digest(document or "")

    # Повторы внутри одного вызова схлопываются здесь, а не в базе: один
    # вызов — это ОДИН том, и оборот, встреченный в нём пять раз, обязан
    # добавить пять вхождений и ровно одну отметку тома. Без этого отметка
    # тома заводилась бы повторно и падала на ограничении уникальности —
    # найдено тестом, а не рассуждением.
    counted: Counter[tuple[str, str]] = Counter()
    for item in values:
        raw_value, raw_display = item if isinstance(item, tuple) else (item, item)
        value = " ".join(str(raw_value).split()).lower()
        if not value:
            continue
        counted[(value, " ".join(str(raw_display).split())[:200])] += 1

    added = 0
    with _session() as db:
        for (value, display), times in counted.items():
            row = db.scalar(select(Observation).where(
                Observation.section == section,
                Observation.kind == kind,
                Observation.value == value))
            if row is None:
                row = Observation(section=section, kind=kind, value=value, display=display)
                db.add(row)
                db.flush()
                added += 1
            row.occurrences += times
            seen = db.scalar(select(Sighting).where(
                Sighting.observation_id == row.id,
                Sighting.document_digest == digest))
            if seen is None:
                db.add(Sighting(observation_id=row.id, document_digest=digest))
                row.documents += 1
        db.commit()
    return added


def profile(section: str | None, kind: str | None = None,
            min_documents: int = 1, limit: int = 0) -> list[ProfileEntry]:
    """Накопленное по разделу, по убыванию числа томов."""
    section = (section or UNKNOWN_SECTION).strip()
    with _session() as db:
        query = select(Observation).where(Observation.section == section,
                                          Observation.documents >= min_documents)
        if kind is not None:
            query = query.where(Observation.kind == kind)
        query = query.order_by(Observation.documents.desc(), Observation.occurrences.desc())
        if limit:
            query = query.limit(limit)
        return [ProfileEntry(kind=r.kind, value=r.value, display=r.display,
                             documents=r.documents, occurrences=r.occurrences)
                for r in db.scalars(query)]


def documents_seen(section: str | None) -> int:
    """По скольким РАЗНЫМ томам этого раздела накоплен профиль."""
    section = (section or UNKNOWN_SECTION).strip()
    with _session() as db:
        rows = db.execute(
            select(func.count(func.distinct(Sighting.document_digest)))
            .join(Observation, Observation.id == Sighting.observation_id)
            .where(Observation.section == section)).scalar()
        return int(rows or 0)


# --- сбор наблюдений из разобранного тома ----------------------------------

# Служебные слова: сами по себе оборота не образуют. Список общий для русского
# языка и к разделу отношения не имеет.
_STOP = frozenset([
    "для", "при", "под", "над", "про", "без", "через", "между", "из-за",
    "из-под", "это", "этот", "эта", "эти", "того", "тому", "тем", "том",
    "так", "как", "что", "чем", "чтобы", "если", "или", "либо", "ибо",
    "тоже", "также", "еще", "ещё", "уже", "лишь", "даже", "него", "нее",
    "неё", "них", "его", "ему", "них", "она", "они", "оно", "все", "всех",
    "всем", "весь", "вся", "всё", "быть", "есть", "был", "была", "было",
    "были", "будет", "будут", "может", "можно", "должен", "должна",
    "должно", "должны", "следует", "необходимо", "требуется",
    "предусмотрено", "предусматривается", "выполняется", "принято",
    "согласно", "соответствии",
])

_WORD_RE = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\-]{3,}")

# Сколько раз оборот должен встретиться В ОДНОМ томе, чтобы считаться
# устойчивым для этого тома. Единица означала бы «любая пара слов», то есть
# профиль забился бы случайными сочетаниями с первого же прогона.
MIN_TERM_REPEATS = 3


def collect_terms(texts: Iterable[str], top_n: int = 40) -> list[tuple[str, str]]:
    """Устойчивые обороты тома: пары соседних значимых слов, повторяющиеся
    в нём не реже `MIN_TERM_REPEATS` раз.

    Метод намеренно грубый и раздело-независимый: никакой морфологии и
    никаких словарей терминов. Задача не в лингвистике, а в том, чтобы
    накопить, ЧЕМ этот раздел обычно говорит, — и сделать это одинаково для
    любого раздела, включая те, которых никто ещё не видел.
    """
    counter: Counter[tuple[str, str]] = Counter()
    for text in texts:
        words = [w.lower() for w in _WORD_RE.findall(text or "")]
        words = [w for w in words if w not in _STOP]
        # strict=False намеренно: списки разной длины по построению —
        # пары соседей на единицу короче самого списка слов.
        for a, b in zip(words, words[1:], strict=False):
            counter[(a, b)] += 1
    out: list[tuple[str, str]] = []
    for (a, b), n in counter.most_common():
        if n < MIN_TERM_REPEATS:
            break
        out.append((f"{a} {b}", f"{a} {b}"))
        if len(out) >= top_n:
            break
    return out


# --- отдача накопленного ----------------------------------------------------

# Сколько РАЗНЫХ томов должны дать оборот, чтобы он попал в подсказку.
# Единица — это привычка одного автора, а не язык раздела: тот же довод, по
# которому Г.21 требует помечать правило, выведенное из одного комплекта,
# статусом n=1. Поэтому подсказка начинает работать со ВТОРОГО тома раздела,
# и это правильно: подсказывать по одному тому значит переносить на раздел
# особенности одного проектировщика.
HINT_MIN_DOCUMENTS = 2


def hint_block(section: str | None, limit: int = 25,
               min_documents: int = HINT_MIN_DOCUMENTS) -> str:
    """Блок подсказки для промпта. Пустая строка, если накопить ещё нечего.

    Формулировка намеренно осторожная: это НЕ список того, что обязано
    найтись. Профиль накоплен на других томах того же раздела и говорит
    только о том, чем такие тома обычно говорят.
    """
    terms = profile(section, kind=KIND_TERM, min_documents=min_documents, limit=limit)
    if not terms:
        return ""
    seen = documents_seen(section)
    listing = ", ".join(f"{t.display} ({t.documents})" for t in terms)
    return (
        f"\nОБОРОТЫ, ВСТРЕЧАВШИЕСЯ В РАЗНЫХ ТОМАХ ЭТОГО РАЗДЕЛА (профиль накоплен\n"
        f"по {seen} {'тому' if seen == 1 else 'томам'}; в скобках — в скольких из них оборот "
        f"встретился). Это НЕ список\nтого, что обязано найтись здесь: факт по-прежнему "
        f"должен быть на странице.\n{listing}\n")


def render_profile(section: str | None) -> str:
    """Раздел отчёта: что программа успела узнать об этом разделе."""
    seen = documents_seen(section)
    label = section or "раздел не определён"
    if not seen:
        return (f"\n=== Что накоплено о разделе «{label}» ===\n"
                "  Данных пока нет: это первый разобранный том такого раздела.\n"
                "  Это не значит, что в разделе ничего не встречается — просто\n"
                "  сравнивать пока не с чем. Профиль пополнится этим прогоном.")
    lines = [f"\n=== Что накоплено о разделе «{label}» "
             f"(по {seen} {'тому' if seen == 1 else 'томам'}) ==="]
    for kind, title in ((KIND_NORM, "нормативы"), (KIND_TABLE, "типы таблиц"),
                        (KIND_TERM, "устойчивые обороты")):
        rows = profile(section, kind=kind, limit=12)
        if not rows:
            continue
        parts = ", ".join(f"{r.display} ({r.documents})" for r in rows)
        lines.append(f"  {title}: {parts}")
    lines.append("  В скобках — в скольких РАЗНЫХ томах наблюдалось. "
                 "Единица означает n=1: один том, один автор.")
    confirmed = len(profile(section, kind=KIND_TERM, min_documents=HINT_MIN_DOCUMENTS))
    lines.append(f"  В подсказку разбора идут только обороты, подтверждённые "
                 f"минимум {HINT_MIN_DOCUMENTS} томами: сейчас таких {confirmed}.")
    return "\n".join(lines)
