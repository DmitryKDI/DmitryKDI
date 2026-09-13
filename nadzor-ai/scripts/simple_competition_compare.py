"""Minimal competition comparator: PD + RD text -> suspicions -> one verifier pass.

This intentionally mirrors the successful interactive GigaChat workflow instead
of running a long autonomous tool loop. Learned inspector memory contributes only
general search habits; it is disabled when --blind is used.
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

from app.inspector_memory import lessons_prompt  # noqa: E402
from app.llm import LlmConfig, call_llm_json, credentials_from_file  # noqa: E402

MAX_PAGE_CHARS = int(os.environ.get("NADZOR_SIMPLE_PAGE_CHARS", "16000"))
MAX_TOTAL_CHARS = int(os.environ.get("NADZOR_SIMPLE_TOTAL_CHARS", "180000"))

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
    # Keep page boundaries and preferentially trim the longest page bodies.
    budget = max(2000, MAX_TOTAL_CHARS // max(1, len(parts)))
    compact = []
    for part in parts:
        compact.append(part if len(part) <= budget else part[:budget] + "\n[page text truncated]")
    return "\n\n".join(compact)[:MAX_TOTAL_CHARS]


def _config(model: str) -> LlmConfig:
    credentials = os.environ.get("GIGACHAT_CREDENTIALS", "").strip() or credentials_from_file("gigachat")
    if not credentials:
        raise RuntimeError("GigaChat credentials not found in environment or local secrets/")
    return LlmConfig(provider="gigachat", api_key=credentials, model=model)


def detect_suspicions(pd_path: Path, rd_paths: Sequence[Path], config: LlmConfig) -> dict:
    documents = _pack_documents(pd_path, rd_paths)
    learned = lessons_prompt()
    prompt = (
        documents
        + learned
        + "\nЗАДАНИЕ: сравни РД с решениями ПД. Найди все инженерно значимые отклонения. "
          "Сначала связывай сущности, затем сравнивай их функцию/параметры/топологию. "
          "Верни только JSON по заданной схеме."
    )
    first = call_llm_json(
        config,
        DETECT_SYSTEM,
        prompt,
        operation="text_verify",
        source_digest="simple-competition-detect:" + str(pd_path) + ":" + "|".join(map(str, rd_paths)),
        prompt_version="simple-competition-v1",
        use_cache=False,
    )
    if not isinstance(first, dict):
        first = {"suspicions": []}
    suspicions = first.get("suspicions") if isinstance(first.get("suspicions"), list) else []
    verify_prompt = (
        documents
        + "\n<SUSPICIONS>\n"
        + json.dumps(suspicions, ensure_ascii=False)
        + "\n</SUSPICIONS>\nПерепроверь только эти подозрения."
    )
    second = call_llm_json(
        config,
        VERIFY_SYSTEM,
        verify_prompt,
        operation="text_verify",
        source_digest="simple-competition-verify:" + str(pd_path) + ":" + "|".join(map(str, rd_paths)),
        prompt_version="simple-competition-verify-v1",
        use_cache=False,
    )
    verification = second.get("verification") if isinstance(second, dict) and isinstance(second.get("verification"), list) else []
    by_id = {str(row.get("id") or ""): row for row in verification if isinstance(row, dict)}
    merged = []
    for item in suspicions:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["verification"] = by_id.get(str(item.get("id") or ""), {"verdict":"needs_more","reason":"verifier returned no row","missing_evidence":[]})
        merged.append(row)
    return {
        "architecture": "simple_pd_rd_two_pass",
        "model": config.resolved_model(),
        "pd": str(pd_path),
        "rd": [str(x) for x in rd_paths],
        "suspicions": merged,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple two-pass PD->RD comparator")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--model", default="GigaChat-3-Ultra")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--blind", action="store_true", help="ignore learned inspector lessons")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.blind:
        os.environ["NADZOR_BLIND_BENCHMARK"] = "1"
    else:
        os.environ["NADZOR_BLIND_BENCHMARK"] = "0"
    result = detect_suspicions(args.pd, args.rd, _config(args.model))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"Saved: {args.output}")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
