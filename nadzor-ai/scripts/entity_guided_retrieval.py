"""Generic entity-guided retrieval for the simple PD/RD comparator.

The retriever is deliberately benchmark-agnostic. It derives anchors only from
the current PD text, scores RD pages by those anchors, and returns a compact
priority context. No expected findings or project-specific identifiers live here.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Callable, Sequence

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9._/+-]{1,}")

_STOP = {
    "система", "системы", "помещение", "помещения", "проект", "рабочая",
    "документация", "предусмотреть", "принять", "выполнить", "оборудование",
    "лист", "листа", "согласно", "общие", "данные", "схема", "схемы",
    "установка", "установки", "воздух", "воздуха", "трубопровод",
}


def _norm(token: str) -> str:
    return token.casefold().strip(".,;:()[]{}<>\"'«»")


def pd_anchors(pd_pages: Sequence[dict], *, limit: int = 80) -> list[str]:
    """Extract useful current-document anchors without any external knowledge."""
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for page in pd_pages:
        for raw in _TOKEN_RE.findall(str(page.get("text") or "")):
            norm = _norm(raw)
            if len(norm) < 3 or norm in _STOP:
                continue
            has_digit = any(ch.isdigit() for ch in norm)
            mark_like = has_digit or "-" in norm or "/" in norm or any(ch.isupper() for ch in raw)
            if mark_like or len(norm) >= 8:
                counts[norm] += 1
                original.setdefault(norm, raw)

    # Prefer specific/rare identifiers and technical terms. Very frequent words
    # are less discriminative, so frequency is used as a small penalty.
    ranked = sorted(
        counts,
        key=lambda t: (
            0 if any(ch.isdigit() for ch in t) else 1,
            counts[t],
            -len(t),
            t,
        ),
    )
    return [original[t] for t in ranked[: max(1, limit)]]


def priority_rd_context(
    pd_pages: Sequence[dict],
    rd_paths: Sequence[Path],
    extract_pages: Callable[[Path], list[dict]],
    *,
    top_pages: int = 10,
    page_chars: int = 7000,
) -> str:
    anchors = pd_anchors(pd_pages)
    anchor_norm = [_norm(x) for x in anchors]
    scored: list[tuple[int, int, str, int, str, list[str]]] = []

    for doc_index, rd_path in enumerate(rd_paths, 1):
        for row in extract_pages(rd_path):
            text = str(row.get("text") or "")
            low = text.casefold()
            hits = [a for a in anchor_norm if a and a in low]
            if not hits:
                continue
            # Numeric/mark anchors count more heavily than long generic terms.
            score = sum(4 if any(ch.isdigit() for ch in a) or "-" in a else 1 for a in set(hits))
            scored.append((score, len(set(hits)), rd_path.name, int(row.get("page") or 0), text, hits))

    scored.sort(key=lambda x: (x[0], x[1], len(x[4])), reverse=True)
    chosen = scored[: max(1, top_pages)]
    if not chosen:
        return ""

    parts = [
        "<PRIORITY_RD_CONTEXT>",
        "Автоматически отобранные страницы РД по сущностям/якорям из текущей ПД. "
        "Это только навигационный приоритет, не ground truth и не доказательство нарушения.",
    ]
    for score, _, name, page, text, hits in chosen:
        unique_hits = list(dict.fromkeys(hits))[:12]
        parts.append(
            f"[priority RD {name} page {page}; score={score}; anchors={', '.join(unique_hits)}]\n"
            + text[:page_chars]
        )
    parts.append("</PRIORITY_RD_CONTEXT>")
    return "\n\n".join(parts)
