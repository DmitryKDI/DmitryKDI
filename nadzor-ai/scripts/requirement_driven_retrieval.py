"""Requirement-driven retrieval for deep PD->RD transfer checks.

The module is deliberately benchmark-agnostic. Requirements and search terms are
extracted only from the current PD. RD pages are ranked independently for every
requirement so unrelated dense pages cannot starve a smaller requirement.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Sequence

_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9._/+-]{1,}")
_STOP = {
    "система", "системы", "помещение", "помещения", "проект", "рабочая",
    "документация", "предусмотреть", "предусмотрен", "предусмотрена",
    "предусмотрены", "выполнить", "должен", "должна", "должны", "лист",
    "листа", "схема", "схемы", "оборудование", "данные", "общие",
}

REQUIREMENT_EXTRACT_SYSTEM = """Ты инженер, который превращает ПД в реестр проверяемых требований.
Извлеки КАЖДОЕ самостоятельно проверяемое инженерное требование, которое можно
сопоставить с РД. Ничего не придумывай и не используй знания о каком-либо эталоне.

Правила:
- сохраняй точные номера помещений, марки систем и оборудования из ПД;
- номер помещения/марка — идентификатор сущности, а не семантическая подсказка;
- разделяй состав, количество, параметр, расположение, подключение, топологию,
  последовательность и покрытие на отдельные требования, если они проверяются отдельно;
- для требования на несколько помещений перечисли все помещения;
- room_types/search_terms нужны только для навигации по РД и не заменяют точные IDs;
- search_terms могут содержать только термины из ПД и нейтральные общеупотребимые
  инженерные варианты/сокращения, без предположений о том, что должно быть найдено в РД.

Верни только JSON:
{"requirements":[{
 "id":"REQ-001",
 "pd_page":1,
 "discipline":"",
 "requirement":"",
 "rooms":[],
 "room_types":[],
 "systems":[],
 "equipment":[],
 "marks":[],
 "functions":[],
 "parameters":[],
 "comparison_dimensions":[],
 "search_terms":[]
}]}"""

REQUIREMENT_COMPARE_SYSTEM = """Ты эксперт-аудитор ПД↔РД. Тебе дан небольшой набор
конкретных требований ПД и специально подобранные для каждого требования страницы РД.
Проверь КАЖДОЕ требование отдельно.

Критические правила:
1. Точный номер помещения, марка системы или оборудования — идентификатор сущности.
   Нельзя заменять его другим помещением/маркой только потому, что назначение похоже.
   Перенумерация допустима только если в документах есть явное соответствие.
2. Сравнивай узлы, связи, роли, количество, параметры, порядок, расположение и scope.
3. Перефразирование при том же инженерном графе не является отклонением.
4. Если релевантный фрагмент РД не найден, не выдумывай отсутствие: используй
   not_observed либо suspicion с needs_human_review=true только когда подобранный
   контекст действительно покрывает нужную сущность/scope и показывает потерю решения.
5. Не пропускай частичное покрытие требования на несколько помещений: проверяй каждое ID.
6. Candidate context — навигация, а не ground truth.

Верни только JSON:
{"results":[{
 "requirement_id":"REQ-001",
 "status":"matched|suspicion|not_observed",
 "reason":"",
 "suspicion":null
}]}
Если status=suspicion, suspicion имеет схему:
{"title":"","discipline":"","requirement_from_pd":"","pd_page":null,
 "pd_quote":"","rd_observation":"","rd_document":"","rd_page":null,
 "rd_quote":"","rooms":[],"systems":[],"equipment":[],
 "difference_type":"absence|quantity|type_change|parameter|location|connection|configuration|topology|coverage|other",
 "reason":"","confidence":0.0,"needs_human_review":true}"""


def _norm(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _tokens(value: object) -> list[str]:
    out: list[str] = []
    for raw in _TOKEN_RE.findall(str(value or "")):
        token = _norm(raw).strip(".,;:()[]{}<>\"'«»")
        if len(token) >= 3 and token not in _STOP:
            out.append(token)
    return list(dict.fromkeys(out))


def normalize_requirements(payload: object, *, limit: int = 28) -> list[dict]:
    rows = payload.get("requirements") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out: list[dict] = []
    for index, raw in enumerate(rows[: max(1, limit)], 1):
        if not isinstance(raw, dict):
            continue
        requirement = str(raw.get("requirement") or "").strip()
        if not requirement:
            continue
        row = dict(raw)
        row["id"] = str(raw.get("id") or f"REQ-{index:03d}")
        try:
            row["pd_page"] = int(raw.get("pd_page") or 0) or None
        except (TypeError, ValueError):
            row["pd_page"] = None
        for key in (
            "rooms", "room_types", "systems", "equipment", "marks", "functions",
            "parameters", "comparison_dimensions", "search_terms",
        ):
            value = raw.get(key)
            if isinstance(value, list):
                row[key] = [str(x).strip() for x in value if str(x).strip()]
            elif value:
                row[key] = [str(value).strip()]
            else:
                row[key] = []
        out.append(row)
    return out


def requirements_prompt(pd_pages: Sequence[dict]) -> str:
    parts = ["<PROJECT_DESIGN>"]
    for row in pd_pages:
        parts.append(f"[PD page {row.get('page')}]\n{row.get('text') or ''}")
    parts.append("</PROJECT_DESIGN>\nИзвлеки полный реестр проверяемых требований ПД.")
    return "\n\n".join(parts)


def _field_terms(requirement: dict) -> dict[str, list[str]]:
    exact_ids: list[str] = []
    for key in ("rooms", "marks"):
        for value in requirement.get(key) or []:
            exact_ids.extend(_tokens(value))

    strong: list[str] = []
    for key in ("systems", "equipment", "parameters"):
        for value in requirement.get(key) or []:
            strong.extend(_tokens(value))

    semantic: list[str] = []
    for key in ("room_types", "functions", "search_terms"):
        for value in requirement.get(key) or []:
            semantic.extend(_tokens(value))
    semantic.extend(_tokens(requirement.get("requirement")))

    return {
        "exact": list(dict.fromkeys(exact_ids)),
        "strong": list(dict.fromkeys(strong)),
        "semantic": list(dict.fromkeys(semantic)),
    }


def _page_score(text: str, requirement: dict) -> tuple[int, dict[str, list[str]]]:
    low = _norm(text)
    terms = _field_terms(requirement)
    hits = {
        key: [term for term in values if term and term in low]
        for key, values in terms.items()
    }
    exact_score = sum(12 if any(ch.isdigit() for ch in x) else 9 for x in hits["exact"])
    strong_score = sum(5 if ("-" in x or "/" in x or any(ch.isdigit() for ch in x)) else 3 for x in hits["strong"])
    semantic_score = min(12, sum(2 if len(x) >= 8 else 1 for x in hits["semantic"]))
    # Exact entity identity must dominate generic functional similarity.
    score = exact_score + strong_score + semantic_score
    return score, hits


def requirement_rd_context(
    requirements: Sequence[dict],
    rd_paths: Sequence[Path],
    extract_pages: Callable[[Path], list[dict]],
    *,
    per_requirement_pages: int = 5,
    per_doc_floor: int = 1,
    page_chars: int = 6500,
) -> tuple[str, list[dict]]:
    """Build independent candidate contexts and return retrieval coverage metadata."""
    all_pages: list[dict] = []
    for doc_index, rd_path in enumerate(rd_paths, 1):
        for row in extract_pages(rd_path):
            all_pages.append({
                "doc_index": doc_index,
                "document": rd_path.name,
                "page": int(row.get("page") or 0),
                "text": str(row.get("text") or ""),
            })

    parts = [
        "<REQUIREMENT_CANDIDATE_CONTEXT>",
        "Страницы ниже выбраны автоматически только из текущих ПД/РД. Они являются "
        "кандидатами для проверки, а не эталоном и не доказательством отклонения.",
    ]
    coverage: list[dict] = []

    for requirement in requirements:
        ranked: list[dict] = []
        for page in all_pages:
            score, hits = _page_score(page["text"], requirement)
            if score <= 0:
                continue
            row = dict(page)
            row.update({"score": score, "hits": hits})
            ranked.append(row)
        ranked.sort(
            key=lambda x: (
                x["score"],
                len(x["hits"]["exact"]),
                len(x["hits"]["strong"]),
                len(x["hits"]["semantic"]),
            ),
            reverse=True,
        )

        selected: list[dict] = []
        # Preserve cross-document coverage before filling globally.
        for doc_index in range(1, len(rd_paths) + 1):
            if len(selected) >= per_requirement_pages:
                break
            doc_rows = [x for x in ranked if x["doc_index"] == doc_index]
            selected.extend(doc_rows[: max(0, per_doc_floor)])
        seen = {(x["doc_index"], x["page"]) for x in selected}
        for row in ranked:
            if len(selected) >= per_requirement_pages:
                break
            key = (row["doc_index"], row["page"])
            if key not in seen:
                selected.append(row)
                seen.add(key)

        req_id = str(requirement.get("id") or "REQ")
        parts.append(
            f"<REQUIREMENT id={json.dumps(req_id, ensure_ascii=False)}>\n"
            + json.dumps(requirement, ensure_ascii=False)
        )
        for row in selected:
            flat_hits = []
            for kind in ("exact", "strong", "semantic"):
                flat_hits.extend(f"{kind}:{x}" for x in row["hits"][kind][:8])
            parts.append(
                f"[candidate for {req_id}: {row['document']} page {row['page']}; "
                f"score={row['score']}; hits={', '.join(flat_hits[:16])}]\n"
                + row["text"][:page_chars]
            )
        parts.append("</REQUIREMENT>")
        coverage.append({
            "requirement_id": req_id,
            "candidate_pages": [
                {
                    "document": x["document"],
                    "page": x["page"],
                    "score": x["score"],
                    "exact_hits": x["hits"]["exact"][:8],
                    "strong_hits": x["hits"]["strong"][:8],
                }
                for x in selected
            ],
        })

    parts.append("</REQUIREMENT_CANDIDATE_CONTEXT>")
    return "\n\n".join(parts), coverage


def requirement_batches(requirements: Sequence[dict], *, size: int = 4) -> list[list[dict]]:
    size = max(1, int(size))
    return [list(requirements[i : i + size]) for i in range(0, len(requirements), size)]
