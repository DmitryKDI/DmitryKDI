"""Generic entity-guided retrieval for the simple PD/RD comparator.

The retriever is deliberately benchmark-agnostic. It derives anchors only from
the current PD text, scores RD pages by those anchors, and returns a compact
priority context. No expected findings or project-specific identifiers live here.

Retrieval is coverage-oriented: instead of allowing one dense topic to consume
all priority slots, it first finds strong RD candidates for each PD page and each
RD document, then fills remaining slots globally. This improves recall for
separate engineering requirements while staying fully document-driven.
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
    "должен", "должна", "должны", "предусмотрена", "предусмотрены",
}


def _norm(token: str) -> str:
    return token.casefold().strip(".,;:()[]{}<>\"'«»")


def _anchor_counts(text: str) -> tuple[Counter[str], dict[str, str]]:
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for raw in _TOKEN_RE.findall(text):
        norm = _norm(raw)
        if len(norm) < 3 or norm in _STOP:
            continue
        has_digit = any(ch.isdigit() for ch in norm)
        mark_like = has_digit or "-" in norm or "/" in norm or any(ch.isupper() for ch in raw)
        if mark_like or len(norm) >= 8:
            counts[norm] += 1
            original.setdefault(norm, raw)
    return counts, original


def _rank_anchors(counts: Counter[str], original: dict[str, str], *, limit: int) -> list[str]:
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


def pd_anchors(pd_pages: Sequence[dict], *, limit: int = 80) -> list[str]:
    """Extract useful current-document anchors without any external knowledge."""
    counts: Counter[str] = Counter()
    original: dict[str, str] = {}
    for page in pd_pages:
        page_counts, page_original = _anchor_counts(str(page.get("text") or ""))
        counts.update(page_counts)
        for key, value in page_original.items():
            original.setdefault(key, value)
    return _rank_anchors(counts, original, limit=limit)


def pd_page_anchor_groups(pd_pages: Sequence[dict], *, per_page_limit: int = 28) -> list[dict]:
    """Return independent anchor sets per PD page to preserve requirement coverage."""
    groups: list[dict] = []
    for row in pd_pages:
        text = str(row.get("text") or "")
        counts, original = _anchor_counts(text)
        anchors = _rank_anchors(counts, original, limit=per_page_limit) if counts else []
        if anchors:
            groups.append({"page": int(row.get("page") or 0), "anchors": anchors})
    return groups


def _score_page(text: str, anchors: Sequence[str]) -> tuple[int, int, list[str]]:
    low = text.casefold()
    hits = [_norm(a) for a in anchors if _norm(a) and _norm(a) in low]
    unique_hits = list(dict.fromkeys(hits))
    if not unique_hits:
        return 0, 0, []

    score = 0
    for anchor in unique_hits:
        has_digit = any(ch.isdigit() for ch in anchor)
        if "-" in anchor or "/" in anchor:
            score += 6
        elif has_digit and len(anchor) >= 4:
            score += 4
        elif has_digit:
            score += 2
        elif len(anchor) >= 10:
            score += 2
        else:
            score += 1
    return score, len(unique_hits), unique_hits


def priority_rd_context(
    pd_pages: Sequence[dict],
    rd_paths: Sequence[Path],
    extract_pages: Callable[[Path], list[dict]],
    *,
    top_pages: int = 10,
    page_chars: int = 7000,
) -> str:
    global_anchors = pd_anchors(pd_pages)
    page_groups = pd_page_anchor_groups(pd_pages)

    rd_rows: list[dict] = []
    for doc_index, rd_path in enumerate(rd_paths, 1):
        for row in extract_pages(rd_path):
            rd_rows.append({
                "doc_index": doc_index,
                "name": rd_path.name,
                "page": int(row.get("page") or 0),
                "text": str(row.get("text") or ""),
            })

    if not rd_rows or not global_anchors:
        return ""

    selected: dict[tuple[int, int], dict] = {}

    def consider(candidate: dict, *, pd_page: int | None, anchors: Sequence[str]) -> None:
        score, hit_count, hits = _score_page(candidate["text"], anchors)
        if score <= 0:
            return
        key = (candidate["doc_index"], candidate["page"])
        row = dict(candidate)
        row.update({
            "score": score,
            "hit_count": hit_count,
            "hits": hits,
            "matched_pd_page": pd_page,
        })
        prev = selected.get(key)
        if prev is None or (score, hit_count) > (prev["score"], prev["hit_count"]):
            selected[key] = row

    # Stage 1: preserve PD coverage. For every PD page, take the best match from
    # every RD document. This prevents one dense discipline/topic from starving
    # other independent requirements.
    coverage_candidates: list[dict] = []
    for group in page_groups:
        for doc_index in range(1, len(rd_paths) + 1):
            best: dict | None = None
            for candidate in rd_rows:
                if candidate["doc_index"] != doc_index:
                    continue
                score, hit_count, hits = _score_page(candidate["text"], group["anchors"])
                if score <= 0:
                    continue
                row = dict(candidate)
                row.update({
                    "score": score,
                    "hit_count": hit_count,
                    "hits": hits,
                    "matched_pd_page": group["page"],
                })
                if best is None or (score, hit_count, len(row["text"])) > (
                    best["score"], best["hit_count"], len(best["text"])
                ):
                    best = row
            if best is not None:
                coverage_candidates.append(best)

    coverage_candidates.sort(
        key=lambda x: (x["score"], x["hit_count"], len(x["text"])), reverse=True
    )
    for row in coverage_candidates:
        if len(selected) >= max(1, top_pages):
            break
        consider(row, pd_page=row["matched_pd_page"], anchors=row["hits"])

    # Stage 2: guarantee at least one useful page per RD document when possible.
    for doc_index in range(1, len(rd_paths) + 1):
        if len(selected) >= max(1, top_pages):
            break
        if any(key[0] == doc_index for key in selected):
            continue
        ranked_doc: list[dict] = []
        for candidate in rd_rows:
            if candidate["doc_index"] != doc_index:
                continue
            score, hit_count, hits = _score_page(candidate["text"], global_anchors)
            if score > 0:
                row = dict(candidate)
                row.update({"score": score, "hit_count": hit_count, "hits": hits, "matched_pd_page": None})
                ranked_doc.append(row)
        if ranked_doc:
            ranked_doc.sort(key=lambda x: (x["score"], x["hit_count"], len(x["text"])), reverse=True)
            best = ranked_doc[0]
            selected[(best["doc_index"], best["page"])] = best

    # Stage 3: fill remaining budget with strongest global matches.
    global_ranked: list[dict] = []
    for candidate in rd_rows:
        score, hit_count, hits = _score_page(candidate["text"], global_anchors)
        if score <= 0:
            continue
        row = dict(candidate)
        row.update({"score": score, "hit_count": hit_count, "hits": hits, "matched_pd_page": None})
        global_ranked.append(row)
    global_ranked.sort(key=lambda x: (x["score"], x["hit_count"], len(x["text"])), reverse=True)
    for row in global_ranked:
        if len(selected) >= max(1, top_pages):
            break
        selected.setdefault((row["doc_index"], row["page"]), row)

    chosen = list(selected.values())[: max(1, top_pages)]
    if not chosen:
        return ""

    parts = [
        "<PRIORITY_RD_CONTEXT>",
        "Автоматически отобранные страницы РД по сущностям/якорям из текущей ПД. "
        "Контекст диверсифицирован по листам ПД и документам РД для увеличения охвата. "
        "Это только навигационный приоритет, не ground truth и не доказательство нарушения.",
    ]
    for row in chosen:
        unique_hits = list(dict.fromkeys(row["hits"]))[:12]
        pd_hint = f"; matched_PD_page={row['matched_pd_page']}" if row["matched_pd_page"] else ""
        parts.append(
            f"[priority RD {row['name']} page {row['page']}; score={row['score']}"
            f"{pd_hint}; anchors={', '.join(unique_hits)}]\n"
            + row["text"][:page_chars]
        )
    parts.append("</PRIORITY_RD_CONTEXT>")
    return "\n\n".join(parts)
