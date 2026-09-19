"""Проверка официальной матрицы с явной полнотой и проверяемыми источниками.

Матрица задаёт, что сравнивать, но не содержит ответов по конкретному объекту.
Ответ модели становится только кандидатом и попадает в результат после проверки
страницы, цитаты и координат по исходному PDF.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Sequence

import pymupdf

from . import facts_store
from .lean_analysis_runtime import run_lean_analysis
from .llm import LlmConfig, call_llm_json
from .llm_runtime import parallel_map
from .parameter_catalog import CATALOG_VERSION, list_parameters
from .vision import UNTRUSTED_INPUT_RULE


def _metadata(document) -> dict:
    return dict(document.source_metadata or {})


def _active_document(stage_documents: list) -> tuple[object | None, str | None]:
    eligible = [doc for doc in stage_documents
                if _metadata(doc).get("approval_status") in {"APPROVED", "FOR_CONSTRUCTION"}]
    if not eligible:
        return None, "нет применимой утверждённой редакции"
    if len(eligible) == 1:
        return eligible[0], None
    predecessors = {_metadata(doc).get("predecessor_id") for doc in eligible}
    terminals = [doc for doc in eligible if doc.id not in predecessors]
    if len(terminals) == 1 and all(
        doc.id == terminals[0].id or doc.id in predecessors for doc in eligible
    ):
        return terminals[0], None
    return None, "несколько редакций без однозначной цепочки замены"


def select_current_documents(documents: Sequence) -> tuple[dict[str, object], dict[str, str]]:
    """Выбирает редакции только по явному статусу и цепочке predecessor."""
    selected: dict[str, object] = {}
    problems: dict[str, str] = {}
    for stage in ("PD", "RD", "ID"):
        candidates = [doc for doc in documents if _metadata(doc).get("stage") == stage]
        if not candidates:
            problems[stage] = "документ стадии не загружен"
            continue
        current, problem = _active_document(candidates)
        if current is None:
            problems[stage] = problem or "актуальная редакция не определена"
        else:
            selected[stage] = current
    return selected, problems


def _facts(document) -> list[dict]:
    facts = facts_store.facts_for(document.file_path, document.name, digest=document.digest)
    return [{"page": int(item["page"]), "text": str(item.get("text") or "")}
            for item in facts.text_facts if item.get("text")]


def _source_text(stage: str, document, facts: list[dict]) -> str:
    metadata = _metadata(document)
    pieces = []
    for item in facts:
        pieces.append(
            f'<page stage="{stage}" document_id="{document.id}" '
            f'page="{item["page"]}">{item["text"]}</page>'
        )
    return (
        f'<document stage="{stage}" document_id="{document.id}" '
        f'code="{metadata.get("document_code", "")}" '
        f'revision="{metadata.get("revision", "")}">\n'
        + "\n".join(pieces) + "\n</document>"
    )


def _relevant_facts(parameters: list[dict], facts_by_stage: dict[str, list[dict]]) -> dict:
    """Ранжирует страницы по словам самой матрицы, не по объектному примеру."""
    query = " ".join(
        str(item.get(field) or "")
        for item in parameters
        for field in ("name", "section", "source_pd", "source_rd", "source_id", "trigger")
    ).casefold()
    terms = {word for word in re.findall(r"[а-яёa-z0-9]+", query) if len(word) >= 4}
    try:
        limit = max(1, int(os.environ.get("NADZOR_OFFICIAL_PAGES_PER_STAGE", "8")))
    except ValueError:
        limit = 8
    selected = {}
    for stage, facts in facts_by_stage.items():
        ranked = []
        for fact in facts:
            text = fact["text"].casefold()
            score = sum(term in text for term in terms)
            if score:
                ranked.append((score, -fact["page"], fact))
        selected[stage] = [item[2] for item in sorted(ranked, reverse=True)[:limit]]
    return selected


def _compact_text(text: str) -> str:
    return " ".join(text.split())


def _verified_evidence(row: dict, selected: dict[str, object], fact_map: dict) -> list[dict]:
    evidence = []
    for source in row.get("evidence") or []:
        if not isinstance(source, dict):
            continue
        stage = str(source.get("stage") or "")
        document = selected.get(stage)
        if document is None or source.get("document_id") != document.id:
            continue
        page = source.get("page")
        quote = str(source.get("quote") or "").strip()
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or not quote:
            continue
        page_text = fact_map.get((stage, page), "")
        if _compact_text(quote) not in _compact_text(page_text):
            continue
        try:
            with pymupdf.open(document.file_path) as pdf:
                if page > pdf.page_count:
                    continue
                pdf_page = pdf[page - 1]
                matches = pdf_page.search_for(quote)
                if not matches:
                    # OCR-текст не обязан иметь PDF-координаты. Такой источник остаётся
                    # видимым, но не выдаётся за полное геометрическое доказательство.
                    bbox = None
                else:
                    rect = matches[0]
                    width, height = pdf_page.rect.width, pdf_page.rect.height
                    bbox = [rect.x0 / width, rect.y0 / height, rect.x1 / width, rect.y1 / height]
        except Exception:  # noqa: BLE001 — источник останется без геометрии, не исчезнет
            bbox = None
        evidence.append({
            "document_id": document.id, "file_id": f"D{document.id}",
            "sha256": document.digest, "stage": stage, "page": page,
            "bbox": bbox, "quote": quote,
        })
    return evidence


def _empty_check(parameter: dict, completeness: str, explanation: str,
                 technical: str = "not_run") -> dict:
    return {
        "finding_id": f'{parameter["code"]}:matrix',
        "parameter_code": parameter["code"], "parameter_name": parameter["name"],
        "priority": parameter["priority"], "completeness_status": completeness,
        "finding_status": None, "expected_value": None, "actual_value": None,
        "explanation": explanation, "evidence": [], "technical_status": technical,
        "review_history": [],
    }


def _ref_evidence(
    refs: list[str],
    bbox: list[float] | None,
    documents: dict[str, object],
    quote: str,
) -> list[dict]:
    evidence = []
    for ref in refs:
        match = re.fullmatch(r"(PD|RD)(\d+):P(\d+)", str(ref).upper())
        if match is None:
            continue
        side, index_text, page_text = match.groups()
        key = "PD" if side == "PD" else f"RD{index_text}"
        document = documents.get(key)
        if document is None:
            continue
        evidence.append({
            "document_id": document.id,
            "file_id": f"D{document.id}",
            "sha256": document.digest,
            "stage": _metadata(document).get("stage"),
            "page": int(page_text),
            "bbox": bbox if len(refs) == 1 else None,
            "quote": quote or None,
        })
    return evidence


def _graphic_check(row: dict, index: int, documents: dict[str, object]) -> dict:
    pd_evidence = _ref_evidence(
        list(row.get("pd_refs") or []),
        row.get("pd_bbox"),
        documents,
        str(row.get("pd_observation") or ""),
    )
    rd_evidence = _ref_evidence(
        list(row.get("rd_refs") or []),
        row.get("rd_bbox"),
        documents,
        str(row.get("rd_observation") or ""),
    )
    evidence = pd_evidence + rd_evidence
    complete = bool(pd_evidence and rd_evidence and all(item.get("bbox") for item in evidence))
    explanation = str(row.get("detail") or row.get("difference") or "").strip()
    return {
        "finding_id": f"graphic:{index}",
        "parameter_code": "GRAPHIC",
        "parameter_name": "Графическое расхождение",
        "priority": "HIGH",
        "completeness_status": "COMPLETE" if complete else "MISSING_EVIDENCE",
        "finding_status": "CANDIDATE",
        "expected_value": str(row.get("pd_observation") or "") or None,
        "actual_value": str(row.get("rd_observation") or "") or None,
        "explanation": explanation or "Графический кандидат требует проверки инспектором.",
        "evidence": evidence,
        "technical_status": "completed",
        "review_history": [],
        "difference_kind": row.get("difference_kind"),
        "requirement_ids": list(row.get("requirement_ids") or []),
    }


def _graphic_analysis(selected: dict[str, object], config: LlmConfig, runner) -> dict:
    pd_document = selected["PD"]
    actual_documents = [selected[stage] for stage in ("RD", "ID") if stage in selected]
    reference_map = {"PD": pd_document}
    reference_map.update({f"RD{index}": document
                          for index, document in enumerate(actual_documents)})
    try:
        result = runner(
            [pd_document.file_path],
            [document.file_path for document in actual_documents],
            llm_config=config,
            before_names=[pd_document.name],
            after_names=[document.name for document in actual_documents],
        )
    except Exception as exc:  # noqa: BLE001 — ошибка остаётся отдельным состоянием
        return {
            "status": "error",
            "reason": f"графическая проверка не выполнена: {type(exc).__name__}",
            "candidates": [],
            "performance": {},
        }
    rows = list(result.get("semantic_findings") or [])
    known = {(tuple(row.get("pd_refs") or []), tuple(row.get("rd_refs") or []),
              str(row.get("detail") or row.get("difference") or "")) for row in rows}
    for row in result.get("semantic_candidates") or []:
        identity = (tuple(row.get("pd_refs") or []), tuple(row.get("rd_refs") or []),
                    str(row.get("detail") or row.get("difference") or ""))
        if identity not in known:
            known.add(identity)
            rows.append(row)
    return {
        "status": "completed" if result.get("valid") else "incomplete",
        "reason": str(result.get("reason") or ""),
        "candidates": [_graphic_check(row, index, reference_map)
                       for index, row in enumerate(rows, start=1)],
        "performance": result.get("performance") or {},
        "diagnostics": result.get("investigator") or {},
    }


def _batch_size() -> int:
    try:
        return max(1, int(os.environ.get("NADZOR_OFFICIAL_BATCH_SIZE", "6")))
    except ValueError:
        return 6


def run_official_analysis(
    documents: Sequence,
    config: LlmConfig,
    *,
    include_medium: bool = False,
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    graphic_runner: Callable[..., dict] | None = run_lean_analysis,
) -> dict:
    """Возвращает результат по каждому параметру; сбой пачки виден отдельно."""
    parameters = [item for item in list_parameters()
                  if item["priority"] == "HIGH" or include_medium]
    total = len(parameters)
    selected, problems = select_current_documents(documents)
    object_id = str(_metadata(documents[0]).get("object_id") or "") if documents else ""
    if progress:
        progress("Проверка комплектности и редакций", 0, total)

    if "PD" not in selected or not ({"RD", "ID"} & selected.keys()):
        missing = "; ".join(f"{stage}: {reason}" for stage, reason in problems.items())
        checks = [_empty_check(item, "MISSING_EVIDENCE", missing) for item in parameters]
        return _result(object_id, checks)

    if not config.api_key:
        checks = [_empty_check(
            item, "CLARIFICATION_REQUIRED",
            "проверка моделью не выполнялась: ключ провайдера не настроен",
        ) for item in parameters]
        return _result(object_id, checks)

    facts_by_stage = {stage: _facts(document) for stage, document in selected.items()}
    fact_map = {(stage, fact["page"]): fact["text"]
                for stage, facts in facts_by_stage.items() for fact in facts}
    digest = hashlib.sha256(
        "|".join(f"{stage}:{selected[stage].digest}" for stage in sorted(selected)).encode()
    ).hexdigest()
    checks = []
    size = _batch_size()
    batches = [(start, parameters[start:start + size]) for start in range(0, total, size)]

    def check_batch(item: tuple[int, list[dict]]) -> tuple[int, list[dict], dict, str]:
        start, batch = item
        batch_error = ""
        if cancelled and cancelled():
            return start, batch, {}, "проверка остановлена инспектором"
        rules = [{key: item[key] for key in (
            "code", "name", "unit", "source_pd", "source_rd", "source_id", "trigger"
        )} for item in batch]
        relevant = _relevant_facts(batch, facts_by_stage)
        source = "\n".join(
            _source_text(stage, selected[stage], relevant[stage]) for stage in selected
        )
        prompt = f"""{UNTRUSTED_INPUT_RULE}
Сопоставь проектные решения ПД с РД и/или ИД только для переданных параметров.
Не проверяй нормы. Не называй технический сбой или нехватку сведений отсутствием
расхождения. Верни JSON: {{"checks":[{{"parameter_code":"M-001",
"assessment":"CANDIDATE|NO_DIFFERENCE_OBSERVED|INSUFFICIENT_EVIDENCE",
"expected_value":"...","actual_value":"...","explanation":"...",
"evidence":[{{"stage":"PD|RD|ID","document_id":1,"page":1,"quote":"точная цитата"}}]}}]}}.
Для кандидата нужны точные цитаты минимум из ПД и одной фактической стадии.
Параметры: {json.dumps(rules, ensure_ascii=False)}
Документы:
{source}"""
        try:
            answer = call_llm_json(
                config, "Ты помощник инспектора. Машинный ответ — гипотеза для проверки.",
                prompt, operation="text_verify", source_digest=digest,
                prompt_version=f"official-matrix-{CATALOG_VERSION}",
            )
            rows = {str(row.get("parameter_code")): row
                    for row in answer.get("checks", []) if isinstance(row, dict)}
        except Exception as exc:  # noqa: BLE001 — вся пачка получает честный error
            rows = {}
            batch_error = f"модель не выполнила проверку: {type(exc).__name__}"
        return start, batch, rows, batch_error

    stopped = False
    for start, batch, rows, batch_error in parallel_map(check_batch, batches):
        if batch_error == "проверка остановлена инспектором":
            stopped = True
            checks.extend(
                _empty_check(parameter, "CLARIFICATION_REQUIRED", batch_error)
                for parameter in batch
            )
        elif stopped:
            checks.extend(
                _empty_check(
                    parameter,
                    "CLARIFICATION_REQUIRED",
                    "проверка остановлена инспектором",
                )
                for parameter in batch
            )
        else:
            if progress:
                progress("Сверка параметров матрицы", start, total)
        for parameter in batch:
            if stopped:
                continue
            row = rows.get(parameter["code"])
            if row is None:
                checks.append(_empty_check(parameter, "NOT_COMPARABLE",
                                           batch_error or "модель не вернула параметр",
                                           "error"))
                continue
            evidence = _verified_evidence(row, selected, fact_map)
            assessment = row.get("assessment")
            candidate = assessment == "CANDIDATE"
            stages = {item["stage"] for item in evidence if item.get("bbox") is not None}
            complete = candidate and "PD" in stages and bool(stages & {"RD", "ID"})
            evidence_complete = "PD" in stages and bool(stages & {"RD", "ID"})
            if candidate:
                completeness = "COMPLETE" if complete else "MISSING_EVIDENCE"
                status = "CANDIDATE"
            elif assessment == "INSUFFICIENT_EVIDENCE":
                completeness, status = "MISSING_EVIDENCE", None
            else:
                completeness = "COMPLETE" if evidence_complete else "MISSING_EVIDENCE"
                status = None
            explanation = str(row.get("explanation") or "")
            if assessment == "NO_DIFFERENCE_OBSERVED":
                explanation = (explanation + " Машинный просмотр не является подтверждённым "
                               "отсутствием нарушения.").strip()
            checks.append({
                **_empty_check(parameter, completeness, explanation, "completed"),
                "finding_status": status,
                "expected_value": str(row.get("expected_value") or "") or None,
                "actual_value": str(row.get("actual_value") or "") or None,
                "evidence": evidence,
            })
    graphic_analysis = {
        "status": "not_run",
        "reason": "графическая проверка не запускалась",
        "candidates": [],
        "performance": {},
    }
    if graphic_runner is not None and not (cancelled and cancelled()):
        if progress:
            progress("Графическая сверка ПД с РД/ИД", min(len(checks), total), total)
        graphic_analysis = _graphic_analysis(selected, config, graphic_runner)
    elif cancelled and cancelled():
        graphic_analysis["reason"] = "графическая проверка остановлена инспектором"
    if progress:
        progress("Формирование протокола", min(len(checks), total), total)
    result = _result(object_id, checks)
    result["graphic_analysis"] = graphic_analysis
    return result


def _result(object_id: str, checks: list[dict]) -> dict:
    completed = sum(item["technical_status"] == "completed" for item in checks)
    return {
        "matrix_version": CATALOG_VERSION, "object_id": object_id, "checks": checks,
        "coverage": {"total": len(checks), "completed": completed,
                     "not_run": len(checks) - completed},
        "graphic_analysis": {
            "status": "not_run",
            "reason": "графическая проверка не выполнялась",
            "candidates": [],
            "performance": {},
        },
    }
