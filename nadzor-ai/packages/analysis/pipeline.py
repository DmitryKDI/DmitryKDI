"""Сборка состояний объекта из файлов: приём, разбор, извлечение фактов."""
from __future__ import annotations

import hashlib
from pathlib import Path

from documents.extract import extract_facts
from documents.pdf import has_text_layer, parse
from documents.schemas import DocKind, StateKind
from documents.stamp import detect_kind, fallback_date, parse_stamp

from analysis.models import DocumentSet, ParsedDoc

STATE_LABELS = {
    StateKind.PD: "Проектная документация",
    StateKind.RD: "Рабочая документация",
    StateKind.EXPERTISE: "Заключение экспертизы",
    StateKind.AS_BUILT: "Исполнительная документация",
    StateKind.PERMIT: "Разрешительная документация",
}


def load_document(path: Path, object_id: str, title: str = "",
                  hint_state: StateKind | None = None) -> ParsedDoc:
    """Разобрать один файл и извлечь факты с координатами."""
    data = path.read_bytes()
    file_id = hashlib.sha256(data).hexdigest()[:16]
    pages = parse(data)
    doc_kind, state_kind = detect_kind(pages)
    if hint_state is not None and doc_kind == DocKind.UNKNOWN:
        state_kind = hint_state
    stamp = parse_stamp(pages)
    doc_id = f"{object_id}-{file_id[:8]}"
    facts = extract_facts(pages, doc_id, file_id)
    return ParsedDoc(
        id=doc_id, file_id=file_id, object_id=object_id, doc_kind=doc_kind,
        state_kind=state_kind, title=title or path.stem, path=str(path),
        revision=stamp.revision or "1",
        doc_date=stamp.doc_date or fallback_date(pages),
        page_count=len(pages), facts=facts,
    ) if has_text_layer(pages) else ParsedDoc(
        id=doc_id, file_id=file_id, object_id=object_id, doc_kind=doc_kind,
        state_kind=state_kind, title=title or path.stem, path=str(path),
        page_count=len(pages), facts=[],
    )


def build_states(docs: list[ParsedDoc], object_id: str) -> list[DocumentSet]:
    """Сгруппировать документы в состояния жизненного цикла.

    Проектная и рабочая документация разделяются по редакциям: разные редакции —
    разные состояния, между которыми возможен переход.
    """
    groups: dict[tuple[StateKind, str], list[ParsedDoc]] = {}
    for doc in docs:
        revision = doc.revision if doc.state_kind in (StateKind.PD, StateKind.RD) else "1"
        groups.setdefault((doc.state_kind, revision), []).append(doc)

    states = []
    ordered = sorted(groups.items(), key=lambda pair: (pair[0][0].value, pair[0][1]))
    for (state_kind, revision), items in ordered:
        label = STATE_LABELS.get(state_kind, state_kind.value)
        if state_kind in (StateKind.PD, StateKind.RD):
            label = f"{label}, редакция {revision}"
        states.append(DocumentSet(object_id=object_id, state_kind=state_kind,
                                  revision=revision, label=label, docs=items))
    return states
