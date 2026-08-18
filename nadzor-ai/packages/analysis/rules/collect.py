"""Выборки документов и фактов из комплекта для правил детектора."""
from __future__ import annotations

from analysis.models import DocumentSet, ParsedDoc
from documents.schemas import DocKind, Fact


def elements_index(state: DocumentSet | None) -> dict[str, tuple[Fact, ParsedDoc]]:
    """Ведомость конструкций: марка конструкции → факт и документ."""
    index: dict[str, tuple[Fact, ParsedDoc]] = {}
    if state is None:
        return index
    for doc in state.of_kind(DocKind.PD, DocKind.RD):
        for fact in doc.facts_of("element"):
            mark = (fact.value or {}).get("mark", "").strip()
            if mark:
                index[mark] = (fact, doc)
    return index


def docs_of(state: DocumentSet | None, kind: DocKind) -> list[ParsedDoc]:
    return [] if state is None else state.of_kind(kind)


def passport_index(state: DocumentSet | None) -> dict[str, ParsedDoc]:
    """Паспорта качества: номер паспорта → документ."""
    out: dict[str, ParsedDoc] = {}
    for doc in docs_of(state, DocKind.PASSPORT):
        number = doc.value("passport_no")
        if number:
            out[number.strip()] = doc
    return out


def report_index(state: DocumentSet | None) -> dict[str, ParsedDoc]:
    """Протоколы испытаний: номер акта освидетельствования → протокол."""
    out: dict[str, ParsedDoc] = {}
    for doc in docs_of(state, DocKind.TEST_REPORT):
        ref = doc.value("aosr_ref").replace("№", "").strip()
        if ref:
            out[ref] = doc
    return out


def act_element(doc: ParsedDoc) -> dict:
    """Конструктивный элемент, к которому относится акт."""
    return {
        "name": _element_name(doc.value("work"), doc.value("mark")),
        "mark": doc.value("mark"),
        "level": doc.value("level"),
        "axes": doc.value("axes"),
        "section": doc.value("section"),
    }


_NAME_HINTS = [
    ("фундамент", "Фундамент"),
    ("колонн", "Колонна"),
    ("плит", "Плита перекрытия"),
    ("стен ядра", "Стена несущая"),
    ("стен", "Стена несущая"),
    ("лестни", "Лестница"),
    ("перегород", "Перегородка"),
    ("кровл", "Кровля"),
    ("стяжк", "Отделка"),
    ("огнезащит", "Металлоконструкции"),
    ("благоустройств", "Благоустройство"),
]


def _element_name(work: str, mark: str) -> str:
    low = f"{work} {mark}".lower()
    for hint, name in _NAME_HINTS:
        if hint in low:
            return name
    return "Конструктивный элемент"
