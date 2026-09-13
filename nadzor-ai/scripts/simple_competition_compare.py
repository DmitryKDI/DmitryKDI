"""Competition comparator: fast whole-document pass plus optional deep requirement pass.

The default path stays cheap for synthetic curriculum. ``--deep-retrieval`` adds
requirement extraction and per-requirement RD retrieval for real transfer checks.
Everything is derived only from the current PD/RD and generic inspector memory.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

import fitz

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "packages" / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

from app.inspector_memory import lessons_prompt  # noqa: E402
from app.llm import LlmConfig, call_llm_json, credentials_from_file  # noqa: E402
from entity_guided_retrieval import priority_rd_context  # noqa: E402
from requirement_driven_retrieval import (  # noqa: E402
    REQUIREMENT_COMPARE_SYSTEM,
    REQUIREMENT_EXTRACT_SYSTEM,
    normalize_requirements,
    requirement_batches,
    requirement_rd_context,
    requirements_prompt,
)

MAX_PAGE_CHARS = int(os.environ.get("NADZOR_SIMPLE_PAGE_CHARS", "16000"))
MAX_TOTAL_CHARS = int(os.environ.get("NADZOR_SIMPLE_TOTAL_CHARS", "180000"))
DEEP_REQUIREMENT_LIMIT = int(os.environ.get("NADZOR_DEEP_REQUIREMENT_LIMIT", "28"))
DEEP_BATCH_SIZE = int(os.environ.get("NADZOR_DEEP_BATCH_SIZE", "4"))

DETECT_SYSTEM = """Ты — эксперт-аудитор инженерной документации.
Твоя задача — найти потенциальные несоответствия между Проектной документацией
(Стадия П) и Рабочей документацией (Стадия РД).

ПД — исходное проектное решение. РД — его реализация.

Работай как инженер, а не как поиск одинаковых слов:
1. Сначала выделяй обязательное решение ПД и его сущности: помещение, система,
   оборудование, функция, параметр, связь, количество, расположение.
2. Затем ищи реализацию той же сущности в РД.
3. Сравнивай presence, quantity, type, parameter, location, connection,
   configuration/topology и coverage.
4. Для требований на несколько помещений проверяй каждое помещение отдельно.
5. Если элемент не найден в доступном объеме РД, это suspicion, а не доказанное
   отсутствие. Не превращай NOT_OBSERVED в ABSENCE.
6. Любое подозрение должно содержать конкретное основание ПД и наблюдение РД,
   а также страницы, если их можно определить.
7. Лучше вернуть дополнительное обоснованное подозрение для проверки человеком,
   чем пропустить технически значимое изменение.
8. Не придумывай нормативы и факты, которых нет в документах.
9. Если ПД и РД совпадают — не создавай нарушение.
10. Отличие формулировки само по себе не является инженерным отклонением. Перед
    созданием suspicion нормализуй смысл в граф сущностей и связей. Если те же
    сущности соединены в том же порядке и имеют те же роли/значения, то активная
    и пассивная форма, обратный порядок слов, синонимы и описание направления
    потока не создают нарушение. Инженерная разница должна менять хотя бы один
    узел, связь, роль, количество, параметр, порядок секций, положение или scope.
11. Блок PRIORITY_RD_CONTEXT является только навигационной подсказкой, автоматически
    выбранной по сущностям текущей ПД. Он не является ground truth. Используй его,
    чтобы не пропускать релевантные страницы, но подтверждай вывод по документам.
12. Номер помещения, марка системы и марка оборудования — точные идентификаторы.
    Не заменяй одну сущность другой только из-за похожего назначения. Сопоставление
    разных IDs допустимо только при явной таблице/указании перенумерации в документах.

Верни только JSON:
{"suspicions":[{
  "id":"SUSP-001",
  "title":"",
  "discipline":"",
  "requirement_from_pd":"",
  "pd_page":null,
  "pd_quote":"",
  "rd_observation":"",
  "rd_document":"",
  "rd_page":null,
  "rd_quote":"",
  "rooms":[],
  "systems":[],
  "equipment":[],
  "difference_type":"absence|quantity|type_change|parameter|location|connection|configuration|topology|coverage|other",
  "reason":"",
  "confidence":0.0,
  "needs_human_review":true
}]}"""

VERIFY_SYSTEM = """Ты — второй независимый проход проверки подозрений ПД↔РД.
Тебе даны исходные тексты документов и список подозрений первого прохода.
Для каждого подозрения проверь: действительно ли ПД содержит заявленное решение,
действительно ли РД отличается, и не перепутано ли отсутствие с ненаблюдаемостью.

Обязательная проверка semantic equivalence перед подтверждением:
- Построй для ПД и РД минимальный граф: сущности -> связи -> роли/параметры.
- confirmed_suspicion допустим только если можно назвать конкретный инженерный delta:
  изменился узел, ребро/подключение, роль, количество, параметр, последовательность,
  расположение или покрытие.
- Если граф и значения совпадают, а различается лишь формулировка, залог,
  направление описания фразы, порядок слов или равнозначные термины — verdict=rejected.
- Номер помещения/марка — идентификатор. Нельзя подтверждать вывод для одного ID
  доказательствами по другому ID без явного соответствия/перенумерации в документах.
- Если доказательств инженерной разницы недостаточно, используй needs_more, а не
  confirmed_suspicion.

Не добавляй новые нарушения в этом проходе.
Верни только JSON:
{"verification":[{
 "id":"SUSP-001",
 "verdict":"confirmed_suspicion|needs_more|rejected",
 "reason":"",
 "missing_evidence":[],
 "confidence":0.0
}]}"""


def _clean_text(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\x00", " ").splitlines() if line.strip())


def extract_pdf_pages(path: Path, *, page_chars: int = MAX_PAGE_CHARS) -> list[dict]:
    rows: list[dict] = []
    with fitz.open(path) as doc:
        for page_number, page in enumerate(doc, 1):
            text = _clean_text(page.get_text("text"))
            rows.append({"page": page_number, "text": text[:page_chars]})
    return rows


def _pack_documents(pd_path: Path, rd_paths: Sequence[Path]) -> str:
    parts: list[str] = ["<PROJECT_DESIGN>"]
    for row in extract_pdf_pages(pd_path):
        parts.append(f"[PD page {row['page']}]\n{row['text']}")
    parts.append("</PROJECT_DESIGN>\n<WORKING_DRAWINGS>")
    for rd_index, rd_path in enumerate(rd_paths, 1):
        parts.append(f"[RD document {rd_index}: {rd_path.name}]")
        for row in extract_pdf_pages(rd_path):
            parts.append(f"[RD{rd_index} page {row['page']}]\n{row['text']}")
    parts.append("</WORKING_DRAWINGS>")
    packed = "\n\n".join(parts)
    if len(packed) <= MAX_TOTAL_CHARS:
        return packed
    budget = max(2000, MAX_TOTAL_CHARS // max(1, len(parts)))
    compact = []
    for part in parts:
        compact.append(part if len(part) <= budget else part[:budget] + "\n[page text truncated]")
    return "\n\n".join(compact)[:MAX_TOTAL_CHARS]


def _priority_context(pd_path: Path, rd_paths: Sequence[Path]) -> str:
    return priority_rd_context(extract_pdf_pages(pd_path), rd_paths, extract_pdf_pages)


def _config(model: str) -> LlmConfig:
    credentials = os.environ.get("GIGACHAT_CREDENTIALS", "").strip() or credentials_from_file("gigachat")
    if not credentials:
        raise RuntimeError("GigaChat credentials not found in environment or local secrets/")
    return LlmConfig(provider="gigachat", api_key=credentials, model=model)


def _extract_requirements(pd_path: Path, config: LlmConfig) -> list[dict]:
    pages = extract_pdf_pages(pd_path)
    payload = call_llm_json(
        config,
        REQUIREMENT_EXTRACT_SYSTEM,
        requirements_prompt(pages),
        operation="text_verify",
        source_digest="requirements:" + str(pd_path),
        prompt_version="requirements-v1",
        use_cache=False,
    )
    return normalize_requirements(payload, limit=DEEP_REQUIREMENT_LIMIT)


def _pd_context_for_batch(pd_pages: Sequence[dict], batch: Sequence[dict]) -> str:
    wanted = {int(x.get("pd_page") or 0) for x in batch if int(x.get("pd_page") or 0) > 0}
    rows = [x for x in pd_pages if int(x.get("page") or 0) in wanted]
    if not rows:
        rows = list(pd_pages)
    parts = ["<PD_EVIDENCE_FOR_REQUIREMENTS>"]
    for row in rows:
        parts.append(f"[PD page {row.get('page')}]\n{row.get('text') or ''}")
    parts.append("</PD_EVIDENCE_FOR_REQUIREMENTS>")
    return "\n\n".join(parts)


def _deep_requirement_pass(
    pd_path: Path,
    rd_paths: Sequence[Path],
    config: LlmConfig,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    requirements = _extract_requirements(pd_path, config)
    if not requirements:
        return [], [], [], []

    pd_pages = extract_pdf_pages(pd_path)
    learned = lessons_prompt()
    suspicions: list[dict] = []
    coverage: list[dict] = []
    audit_rows: list[dict] = []

    for batch_index, batch in enumerate(requirement_batches(requirements, size=DEEP_BATCH_SIZE), 1):
        candidate_context, batch_coverage = requirement_rd_context(
            batch,
            rd_paths,
            extract_pdf_pages,
        )
        coverage.extend(batch_coverage)
        prompt = (
            _pd_context_for_batch(pd_pages, batch)
            + "\n\n<REQUIREMENT_SET>\n"
            + json.dumps(batch, ensure_ascii=False)
            + "\n</REQUIREMENT_SET>\n\n"
            + candidate_context
            + learned
            + "\nПроверь каждое требование из REQUIREMENT_SET. Верни строку результата для каждого ID."
        )
        payload = call_llm_json(
            config,
            REQUIREMENT_COMPARE_SYSTEM,
            prompt,
            operation="text_verify",
            source_digest=(
                "requirement-compare:"
                + str(pd_path)
                + ":"
                + "|".join(map(str, rd_paths))
                + f":batch{batch_index}"
            ),
            prompt_version="requirement-compare-v1",
            use_cache=False,
        )
        rows = payload.get("results") if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []
        batch_ids = {str(x.get("id") or "") for x in batch}
        seen_ids: set[str] = set()
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            req_id = str(raw.get("requirement_id") or "")
            if req_id not in batch_ids:
                continue
            seen_ids.add(req_id)
            status = str(raw.get("status") or "not_observed").strip().lower()
            audit_rows.append({
                "requirement_id": req_id,
                "status": status,
                "reason": str(raw.get("reason") or ""),
            })
            suspicion = raw.get("suspicion")
            if status == "suspicion" and isinstance(suspicion, dict):
                row = dict(suspicion)
                row["source_requirement_id"] = req_id
                row["origin"] = "requirement_driven"
                suspicions.append(row)
        for req_id in sorted(batch_ids - seen_ids):
            audit_rows.append({
                "requirement_id": req_id,
                "status": "not_observed",
                "reason": "requirement comparator returned no row",
            })

    return requirements, coverage, audit_rows, suspicions


def _norm_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({" ".join(str(x).casefold().split()) for x in value if str(x).strip()}))


def _dedupe_suspicions(rows: Sequence[dict]) -> list[dict]:
    chosen: dict[tuple, dict] = {}
    order: list[tuple] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        req_id = str(row.get("source_requirement_id") or "")
        rooms = _norm_list(row.get("rooms"))
        equipment = _norm_list(row.get("equipment"))
        systems = _norm_list(row.get("systems"))
        kind = str(row.get("difference_type") or "").casefold().strip()
        if req_id:
            key = ("req", req_id, kind)
        elif rooms or equipment or systems:
            key = ("entity", rooms, equipment[:4], systems[:4], kind)
        else:
            key = ("text", " ".join(str(row.get("title") or "").casefold().split()), kind)
        previous = chosen.get(key)
        if previous is None:
            chosen[key] = row
            order.append(key)
        elif float(row.get("confidence") or 0.0) > float(previous.get("confidence") or 0.0):
            chosen[key] = row
    out = [chosen[key] for key in order]
    for index, row in enumerate(out, 1):
        row["id"] = f"SUSP-{index:03d}"
    return out


def detect_suspicions(
    pd_path: Path,
    rd_paths: Sequence[Path],
    config: LlmConfig,
    *,
    deep_retrieval: bool = False,
) -> dict:
    documents = _pack_documents(pd_path, rd_paths)
    priority = _priority_context(pd_path, rd_paths)
    learned = lessons_prompt()
    prompt = (
        (priority + "\n\n" if priority else "")
        + documents
        + learned
        + "\nЗАДАНИЕ: сравни РД с решениями ПД. Найди все инженерно значимые отклонения. "
          "Сначала связывай сущности, затем сравнивай их функцию/параметры/топологию. "
          "Не считай перефразирование инженерным изменением и не подменяй точные IDs. "
          "Верни только JSON по заданной схеме."
    )
    first = call_llm_json(
        config,
        DETECT_SYSTEM,
        prompt,
        operation="text_verify",
        source_digest="simple-competition-detect:" + str(pd_path) + ":" + "|".join(map(str, rd_paths)),
        prompt_version="simple-competition-v4-exact-ids",
        use_cache=False,
    )
    if not isinstance(first, dict):
        first = {"suspicions": []}
    suspicions = first.get("suspicions") if isinstance(first.get("suspicions"), list) else []
    suspicions = [dict(x, origin="whole_document") for x in suspicions if isinstance(x, dict)]

    requirements: list[dict] = []
    requirement_coverage: list[dict] = []
    requirement_results: list[dict] = []
    if deep_retrieval:
        requirements, requirement_coverage, requirement_results, deep_rows = _deep_requirement_pass(
            pd_path, rd_paths, config
        )
        suspicions.extend(deep_rows)

    suspicions = _dedupe_suspicions(suspicions)
    verify_prompt = (
        (priority + "\n\n" if priority else "")
        + documents
        + "\n<SUSPICIONS>\n"
        + json.dumps(suspicions, ensure_ascii=False)
        + "\n</SUSPICIONS>\nПерепроверь только эти подозрения. Строго проверь точные IDs сущностей и semantic equivalence."
    )
    second = call_llm_json(
        config,
        VERIFY_SYSTEM,
        verify_prompt,
        operation="text_verify",
        source_digest="simple-competition-verify:" + str(pd_path) + ":" + "|".join(map(str, rd_paths)),
        prompt_version="simple-competition-verify-v3-exact-ids",
        use_cache=False,
    )
    verification = second.get("verification") if isinstance(second, dict) and isinstance(second.get("verification"), list) else []
    by_id = {str(row.get("id") or ""): row for row in verification if isinstance(row, dict)}
    merged: list[dict] = []
    rejected: list[dict] = []
    for item in suspicions:
        row = dict(item)
        check = by_id.get(
            str(item.get("id") or ""),
            {"verdict":"needs_more","reason":"verifier returned no row","missing_evidence":[]},
        )
        row["verification"] = check
        if str(check.get("verdict") or "").strip().lower() == "rejected":
            rejected.append(row)
        else:
            merged.append(row)

    return {
        "architecture": (
            "hybrid_whole_document+requirement_driven_retrieval+verifier"
            if deep_retrieval
            else "simple_pd_rd_two_pass+entity_guided_retrieval"
        ),
        "model": config.resolved_model(),
        "pd": str(pd_path),
        "rd": [str(x) for x in rd_paths],
        "priority_context_used": bool(priority),
        "deep_retrieval_used": bool(deep_retrieval),
        "requirements_extracted": requirements,
        "requirement_coverage": requirement_coverage,
        "requirement_results": requirement_results,
        "suspicions": merged,
        "rejected_suspicions": rejected,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Two-pass PD->RD comparator with optional deep requirement retrieval")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--blind", action="store_true", help="ignore learned inspector lessons")
    parser.add_argument(
        "--deep-retrieval",
        action="store_true",
        help="extract PD requirements and compare each against independently retrieved RD pages",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["NADZOR_BLIND_BENCHMARK"] = "1" if args.blind else "0"
    result = detect_suspicions(
        args.pd,
        args.rd,
        _config(args.model),
        deep_retrieval=bool(args.deep_retrieval),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Saved: {args.output}")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
