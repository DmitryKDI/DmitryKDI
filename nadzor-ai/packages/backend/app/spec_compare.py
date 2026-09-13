"""СВЕРКА ВЕДОМОСТЕЙ — список оборудования ПД против рабочей документации.

Зачем отдельно от сверки прозы. Позиция ведомости — не требование к объекту,
а предмет поставки: наименование изделия с маркой. Проверять её так же, как
предписание («ограждать защитными экранами»), нельзя — вопрос здесь другой:
есть ли эта марка в рабочей документации, заменена ли она на другую, сошлось
ли количество. Это сравнение ДВУХ СПИСКОВ, и оно делается разом, без
просмотра чертежей и без вызовов модели.

На реальном комплекте прежний способ дал десятки строк «требует проверки»
на одинаковых изделиях и ложные подтверждения по имени завода: марка
считалась подтверждённой, потому что где-то в рабочей документации
встречалось название производителя из соседнего предложения. Присутствие
бренда не доказывает присутствие изделия (Г.117).

Сравнение идёт по ОБОЗНАЧЕНИЮ, приведённому к одному виду: регистр, пробелы
вокруг дефисов и кириллические буквы-двойники («х» вместо «x»), которые в
документах встречаются вперемешку.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Ссылка на норматив обозначением изделия не является: номер стандарта —
# документ, а не марка.
_NORM_REF_RE = re.compile(r"\b(?:ГОСТ|СП|СНиП|СанПиН|ТУ|ISO|EN|DIN)\b[\s№]*[\d.\-/]+",
                          re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/.хx×]*")

# Кириллические двойники латиницы: в марках они встречаются вперемешку с
# латинскими, и без приведения «400-80A-3х10» и «400-80A-3x10» — разные
# строки, хотя это одно изделие.
_LOOKALIKE = str.maketrans({"х": "x", "Х": "X", "А": "A", "В": "B", "С": "C",
                            "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
                            "Р": "P", "Т": "T", "У": "Y"})

STATUS_PRESENT = "есть в РД"
STATUS_MISSING = "нет в РД"
STATUS_SIMILAR = "в РД другая марка"


def normalize(designation: str) -> str:
    text = designation.translate(_LOOKALIKE).upper()
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def designations(sentence: str) -> list[str]:
    """Обозначения изделий в строке ведомости.

    Обозначение — цепочка соседних латинско-цифровых токенов, где хотя бы
    один несёт цифру: «WNK 160/1», «KDV DU 400-80A-3х10», «KF-IW-22B-V».
    Цепочкой, а не одним токеном, потому что в документах марка и
    типоразмер разнесены пробелом ровно так же часто, как слитно.
    """
    clean = _NORM_REF_RE.sub(" ", sentence or "")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run and any(any(c.isdigit() for c in t) for t in run) \
                and any(any(c.isalpha() for c in t) for t in run):
            out.append(normalize(" ".join(run)))
        run.clear()

    position = 0
    for match in _TOKEN_RE.finditer(clean):
        # Разрыв — любой символ между токенами, кроме одиночного пробела:
        # «WNK 160/1» одно обозначение, «WNK 160/1, 1 компл.» — не одно.
        if run and clean[position:match.start()] != " ":
            flush()
        run.append(match.group(0))
        position = match.end()
    flush()
    return out


@dataclass
class SpecEntry:
    """Одна позиция ведомости проектной документации."""
    designation: str
    sentence: str = ""
    document: str = ""
    section: str | None = None
    pages: list[int] = field(default_factory=list)


@dataclass
class SpecDiff:
    entry: SpecEntry
    status: str
    detail: str = ""


@dataclass
class SpecResult:
    diffs: list[SpecDiff] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def by_status(self, status: str) -> list[SpecDiff]:
        return [d for d in self.diffs if d.status == status]


def collect(requirements) -> list[SpecEntry]:
    """Позиции ведомости из строк разбора ПД, одинаковые марки — одной
    записью со списком страниц: сорок строк про одну установку инспектору
    не нужны, а страницы нужны все."""
    merged: dict[tuple[str, str], SpecEntry] = {}
    for req in requirements:
        sentence = getattr(req, "sentence", "") or ""
        for designation in designations(sentence):
            key = (designation, getattr(req, "document", "") or "")
            entry = merged.get(key)
            if entry is None:
                entry = SpecEntry(designation=designation, sentence=sentence,
                                  document=getattr(req, "document", "") or "",
                                  section=getattr(req, "section", None))
                merged[key] = entry
            page = int(getattr(req, "page", 0) or 0)
            if page and page not in entry.pages:
                entry.pages.append(page)
    return list(merged.values())


def _prefix(designation: str) -> str:
    """Буквенная часть марки: по ней ищется замена на другой типоразмер."""
    match = re.match(r"[A-Z][A-Z\-]*", designation)
    return match.group(0) if match else ""


def compare(entries: list[SpecEntry], rd_text: str) -> SpecResult:
    """Сверить позиции ПД с текстом рабочей документации."""
    haystack = normalize(rd_text or "")
    rd_designations = set()
    for line in (rd_text or "").splitlines():
        rd_designations.update(designations(line))

    result = SpecResult()
    for entry in entries:
        if entry.designation and entry.designation in haystack:
            result.diffs.append(SpecDiff(entry, STATUS_PRESENT,
                                         "обозначение найдено в тексте рабочей документации"))
            continue
        prefix = _prefix(entry.designation)
        similar = sorted({d for d in rd_designations
                          if prefix and _prefix(d) == prefix and d != entry.designation})
        if similar:
            result.diffs.append(SpecDiff(
                entry, STATUS_SIMILAR,
                "в рабочей документации того же ряда: " + ", ".join(similar[:5])))
        else:
            result.diffs.append(SpecDiff(
                entry, STATUS_MISSING,
                "обозначение в тексте рабочей документации не найдено; "
                "это не вывод об отсутствии изделия: подписи чертежей часто в кривых"))
    counts: dict[str, int] = {}
    for diff in result.diffs:
        counts[diff.status] = counts.get(diff.status, 0) + 1
    result.counts = counts
    return result


def render(result: SpecResult) -> str:
    """Отчёт по ведомости — в том же виде, что сводка разбора: сначала то,
    что требует внимания, затем совпавшее."""
    if not result.diffs:
        return ""
    lines = ["=== Ведомость оборудования: ПД против рабочей документации ===",
             "Сравнение двух списков по обозначению изделия. Отсутствие обозначения",
             "в тексте РД не является выводом об отсутствии изделия: на чертежах",
             "подписи часто переведены в кривые, и тогда смотреть нужно глазами.",
             ""]
    for status in (STATUS_SIMILAR, STATUS_MISSING, STATUS_PRESENT):
        group = result.by_status(status)
        if not group:
            continue
        lines.append(f"--- {status} ({len(group)}) ---")
        for diff in sorted(group, key=lambda d: d.entry.designation):
            entry = diff.entry
            where = ", ".join(str(p) for p in sorted(entry.pages))
            section = f"[{entry.section}] " if entry.section else ""
            lines.append(f"  {section}{entry.designation} — {entry.document} стр.{where}")
            if status != STATUS_PRESENT:
                lines.append(f"      {diff.detail}")
        lines.append("")
    return "\n".join(lines).rstrip()
