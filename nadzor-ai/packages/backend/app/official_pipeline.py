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
from .llm import LlmConfig, call_llm_json, model_configured
from .llm_runtime import parallel_map
from .parameter_catalog import current_matrix_version, list_parameters
from .protocol import numeric_delta
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
    dated = []
    for document in terminals:
        raw_date = _metadata(document).get("approval_date")
        if raw_date:
            dated.append((str(raw_date), document))
    if dated:
        latest = max(item[0] for item in dated)
        latest_documents = [document for date, document in dated if date == latest]
        if len(latest_documents) == 1:
            return latest_documents[0], None
    return None, "несколько редакций без однозначной цепочки замены"


def select_current_documents(documents: Sequence) -> tuple[dict[str, list], dict[str, str]]:
    """Выбирает актуальную редакцию каждого шифра, не схлопывая комплект в один том."""
    selected: dict[str, list] = {}
    problems: dict[str, str] = {}
    for stage in ("PD", "RD", "ID"):
        candidates = [doc for doc in documents if _metadata(doc).get("stage") == stage]
        if not candidates:
            problems[stage] = "документ стадии не загружен"
            continue
        groups: dict[str, list] = {}
        for document in candidates:
            code = str(_metadata(document).get("document_code") or "").strip()
            groups.setdefault(code, []).append(document)
        active = []
        stage_problems = []
        for code, revisions in groups.items():
            current, problem = _active_document(revisions)
            if current is None:
                stage_problems.append(f"{code or 'шифр не задан'}: {problem}")
            else:
                active.append(current)
        if stage_problems:
            problems[stage] = "; ".join(stage_problems)
        if active:
            selected[stage] = sorted(
                active,
                key=lambda document: (
                    str(_metadata(document).get("document_code") or ""), document.id
                ),
            )
    return selected, problems


def _facts(document) -> list[dict]:
    facts = facts_store.facts_for(document.file_path, document.name, digest=document.digest)
    return [{"page": int(item["page"]), "text": str(item.get("text") or "")}
            for item in facts.text_facts if item.get("text")]


def _ocr_quality(documents: list) -> dict[int, dict]:
    """Покрываемость распознавания по документам (ТЗ 9.1, п.1).

    Нечитаемые страницы не пропадают молча: доля и номера листов с
    LOW_QUALITY и ABSTAIN попадают в протокол рядом с результатом.
    """
    summary = {}
    for document in documents:
        facts = facts_store.facts_for(document.file_path, document.name,
                                      digest=document.digest)
        quality = dict(getattr(facts, "ocr_quality", {}) or {})
        low = sorted(page for page, value in quality.items() if value == "LOW_QUALITY")
        abstain = sorted(page for page, value in quality.items() if value == "ABSTAIN")
        pages = int(getattr(facts, "pages", 0) or 0)
        summary[document.id] = {
            "recognized_pages": len(quality), "low_quality_pages": low,
            "abstain_pages": abstain,
            "coverage": round(1 - len(abstain) / pages, 4) if pages else None,
        }
    return summary


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


def _relevant_facts(parameters: list[dict], facts_by_document: dict[int, list[dict]]) -> dict:
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
    # Шаблоны разбора из таблицы Params (ТЗ 8.1, regex_pattern): страница, где
    # шаблон нашёл значение, ставится выше страниц, совпавших только словами.
    patterns = []
    for item in parameters:
        raw = str(item.get("regex_pattern") or "").strip()
        if not raw:
            continue
        try:
            patterns.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            continue  # ошибочный шаблон администратора не роняет проверку
    selected = {}
    for document_id, facts in facts_by_document.items():
        ranked = []
        for fact in facts:
            text = fact["text"].casefold()
            score = sum(term in text for term in terms)
            regex_hits = sum(bool(pattern.search(fact["text"])) for pattern in patterns)
            # Нулевое совпадение не означает нерелевантность: OCR мог исказить
            # заголовок, а значение остаться читаемым. Поэтому здесь ранжирование,
            # а не отсев страниц.
            ranked.append((regex_hits, score, -fact["page"], fact))
        selected[document_id] = [item[-1] for item in sorted(ranked, key=lambda row: row[:3],
                                                              reverse=True)[:limit]]
    return selected


def _compact_text(text: str) -> str:
    return " ".join(text.split())


def _verified_evidence(row: dict, selected: dict[str, list], fact_map: dict,
                       ocr_map: dict | None = None) -> list[dict]:
    evidence = []
    for source in row.get("evidence") or []:
        if not isinstance(source, dict):
            continue
        stage = str(source.get("stage") or "")
        document = next(
            (item for item in selected.get(stage, []) if item.id == source.get("document_id")),
            None,
        )
        if document is None:
            continue
        page = source.get("page")
        quote = str(source.get("quote") or "").strip()
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or not quote:
            continue
        page_text = fact_map.get((document.id, page), "")
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
            "role": "expected" if stage == "PD" else "actual",
            "bbox": bbox, "quote": quote,
            # Цитата со страницы, распознанной с низким качеством, остаётся
            # доказательством, но инспектор видит, что её прочитала машина.
            "ocr_quality": (ocr_map or {}).get((document.id, page)),
        })
    return evidence


def _empty_check(parameter: dict, completeness: str, explanation: str,
                 technical: str = "not_run") -> dict:
    return {
        "finding_id": f'{parameter["code"]}:matrix',
        "parameter_code": parameter["code"], "parameter_name": parameter["name"],
        "priority": parameter["priority"], "completeness_status": completeness,
        "rule": parameter.get("trigger"), "finding_type": "matrix_difference",
        "finding_status": None, "expected_value": None, "actual_value": None,
        "explanation": explanation, "evidence": [], "technical_status": technical,
        "review_history": [], "confidence": None,
        "section": parameter.get("section"),
        # Нормативное основание параметра из таблицы Params (ТЗ 8.1).
        "normative_refs": {key: parameter.get(key) for key in (
            "sp_reference", "gost_reference", "fz_reference", "other_normative")
            if parameter.get(key)},
    }


def _rule(parameter: dict) -> dict:
    """Правило параметра для модели: поля матрицы и пороги администратора."""
    rule = {key: parameter.get(key) for key in (
        "code", "name", "unit", "source_pd", "source_rd", "source_id", "trigger")}
    for key in ("data_type", "min_value", "max_value"):
        if parameter.get(key) not in (None, ""):
            rule[key] = parameter[key]
    return rule


def _first_number(value) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value or ""))
    return float(match.group().replace(",", ".")) if match else None


def threshold_status(parameter: dict, actual) -> str | None:
    """Порог из таблицы Params (min_value, max_value) проверяется кодом, а не моделью.

    OUT_OF_RANGE — фактическое значение вне допустимого диапазона; WITHIN —
    в диапазоне; None — порог не задан или значение не число. Это сигнал для
    инспектора, а не вывод о нарушении.
    """
    low, high = parameter.get("min_value"), parameter.get("max_value")
    if low is None and high is None:
        return None
    number = _first_number(actual)
    if number is None:
        return None
    if (low is not None and number < low) or (high is not None and number > high):
        return "OUT_OF_RANGE"
    return "WITHIN"


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
        key = f"{side}{index_text}"
        document = documents.get(key)
        if document is None:
            continue
        evidence.append({
            "document_id": document.id,
            "file_id": f"D{document.id}",
            "sha256": document.digest,
            "stage": _metadata(document).get("stage"),
            "role": "expected" if side == "PD" else "actual",
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
    raw_confidence = row.get("confidence")
    confidence = (
        float(raw_confidence)
        if isinstance(raw_confidence, int | float)
        and not isinstance(raw_confidence, bool)
        and 0 <= raw_confidence <= 1
        else None
    )
    return {
        "finding_id": f"graphic:{index}",
        "parameter_code": "GRAPHIC",
        "parameter_name": "Графическое расхождение",
        "rule": "сопоставление графического решения ПД с РД/ИД",
        "finding_type": "graphic_difference",
        "priority": "HIGH",
        "completeness_status": "COMPLETE" if complete else "MISSING_EVIDENCE",
        # Гипотеза вне матрицы становится кандидатом только с источниками и
        # координатами с обеих сторон; без них это SUSPICION (ТЗ 9.5): её
        # видно, но в число расхождений и в обучение она не входит.
        "finding_status": "CANDIDATE" if complete else "SUSPICION",
        "discovery_method": "GRAPHIC_COMPARISON",
        "expected_value": str(row.get("pd_observation") or "") or None,
        "actual_value": str(row.get("rd_observation") or "") or None,
        "explanation": explanation or "Графический кандидат требует проверки инспектором.",
        "evidence": evidence,
        "technical_status": "completed",
        "review_history": [],
        "difference_kind": row.get("difference_kind"),
        "requirement_ids": list(row.get("requirement_ids") or []),
        "confidence": confidence,
    }


def _graphic_analysis(selected: dict[str, list], config: LlmConfig, runner) -> dict:
    pd_documents = selected["PD"]
    actual_documents = [document for stage in ("RD", "ID")
                        for document in selected.get(stage, [])]
    reference_map = {f"PD{index}": document
                     for index, document in enumerate(pd_documents)}
    reference_map.update({f"RD{index}": document
                          for index, document in enumerate(actual_documents)})
    try:
        result = runner(
            [document.file_path for document in pd_documents],
            [document.file_path for document in actual_documents],
            llm_config=config,
            before_names=[document.name for document in pd_documents],
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


def _with_document_selection(result: dict, selected: dict[str, list], problems: dict) -> dict:
    result["document_selection"] = {
        "selected": {stage: [document.id for document in stage_documents]
                     for stage, stage_documents in selected.items()},
        "problems": problems,
    }
    return result


def run_official_analysis(
    documents: Sequence,
    config: LlmConfig,
    *,
    include_medium: bool = False,
    progress: Callable[[str, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    graphic_runner: Callable[..., dict] | None = run_lean_analysis,
) -> dict:
    """Возвращает результат по каждому параметру; сбой пачки виден отдельно.

    Сверяются все активные параметры матрицы (ТЗ 9.2, п.3: «для каждого
    параметра из 132»). Приоритет влияет только на очередность экспертной
    проверки, а не на то, проверяется ли параметр. Флаг include_medium
    оставлен для совместимости вызова и на состав проверки не влияет.
    """
    parameters = list_parameters()
    total = len(parameters)
    selected, problems = select_current_documents(documents)
    object_id = str(_metadata(documents[0]).get("object_id") or "") if documents else ""
    if progress:
        progress("Проверка комплектности и редакций", 0, total)

    if "PD" not in selected or not ({"RD", "ID"} & selected.keys()):
        missing = "; ".join(f"{stage}: {reason}" for stage, reason in problems.items())
        checks = [_empty_check(item, "MISSING_EVIDENCE", missing) for item in parameters]
        return _with_document_selection(_result(object_id, checks), selected, problems)

    if not model_configured(config):
        # Не «нарушений нет», а «проверка не выполнялась» — и по каждому
        # параметру отдельно, чтобы отчёт не выглядел пройденным (Г.10).
        checks = [_empty_check(
            item, "CLARIFICATION_REQUIRED",
            "официальная проверка не выполнялась: локальная модель не подключена",
        ) for item in parameters]
        return _with_document_selection(_result(object_id, checks), selected, problems)

    selected_documents = [document for stage in ("PD", "RD", "ID")
                          for document in selected.get(stage, [])]
    facts_by_document = {document.id: _facts(document) for document in selected_documents}
    fact_map = {(document_id, fact["page"]): fact["text"]
                for document_id, facts in facts_by_document.items() for fact in facts}
    ocr_summary = _ocr_quality(selected_documents)
    ocr_map = {}
    for document in selected_documents:
        facts = facts_store.facts_for(document.file_path, document.name,
                                      digest=document.digest)
        for page, value in (getattr(facts, "ocr_quality", {}) or {}).items():
            ocr_map[(document.id, int(page))] = value
    digest = hashlib.sha256(
        "|".join(f"{_metadata(document).get('stage')}:{document.digest}"
                 for document in selected_documents).encode()
    ).hexdigest()
    checks = []
    selection_note = "; ".join(
        f"{stage}: {problem}" for stage, problem in problems.items() if stage in selected
    )
    matrix_version = current_matrix_version()
    size = _batch_size()
    batches = [(start, parameters[start:start + size]) for start in range(0, total, size)]

    def check_batch(item: tuple[int, list[dict]]) -> tuple[int, list[dict], dict, str]:
        start, batch = item
        batch_error = ""
        if cancelled and cancelled():
            return start, batch, {}, "проверка остановлена инспектором"
        rules = [_rule(item) for item in batch]
        relevant = _relevant_facts(batch, facts_by_document)
        source = "\n".join(
            _source_text(str(_metadata(document).get("stage")), document,
                         relevant[document.id])
            for document in selected_documents
        )
        prompt = f"""{UNTRUSTED_INPUT_RULE}
 Сопоставь проектные решения ПД с РД и/или ИД только для переданных параметров.
 Не выполняй самостоятельную проверку по нормативам и не дополняй матрицу нормами.
 Порог из матрицы используй только как правило сравнения документов.
 Не называй технический сбой или нехватку сведений отсутствием
расхождения. Верни JSON: {{"checks":[{{"parameter_code":"M-001",
"assessment":"CANDIDATE|NO_DIFFERENCE_OBSERVED|INSUFFICIENT_EVIDENCE|NOT_APPLICABLE",
"expected_value":"...","actual_value":"...","explanation":"...","confidence":0.0,
"approved_change_ref":"NONE или ссылка на ведомость/лист согласованного изменения",
"evidence":[{{"stage":"PD|RD|ID","document_id":1,"page":1,"quote":"точная цитата"}}]}}]}}.
Для кандидата нужны точные цитаты минимум из ПД и одной фактической стадии.
NOT_APPLICABLE — только если документы прямо показывают, что параметр к
объекту не относится; в explanation укажи основание с цитатой.
Параметры: {json.dumps(rules, ensure_ascii=False)}
Документы:
{source}"""
        try:
            answer = call_llm_json(
                config, "Ты помощник инспектора. Машинный ответ — гипотеза для проверки.",
                prompt, operation="text_verify", source_digest=digest,
                prompt_version=f"official-matrix-{matrix_version}",
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
            evidence = _verified_evidence(row, selected, fact_map, ocr_map)
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
            elif assessment == "NOT_APPLICABLE" and evidence:
                # Неприменимость — утверждение, и у него обязано быть основание
                # в документе; без цитаты это просто «не нашли» (Г.10).
                completeness, status = "NOT_APPLICABLE", None
            else:
                completeness = "COMPLETE" if evidence_complete else "MISSING_EVIDENCE"
                # Сопоставимые источники проверены с обеих сторон, расхождения
                # нет: предварительный NEGATIVE_VERIFIED (ТЗ 9.2). Без
                # доказательств с обеих сторон статуса нет вовсе.
                status = ("NEGATIVE_VERIFIED"
                          if assessment == "NO_DIFFERENCE_OBSERVED" and evidence_complete
                          else None)
            explanation = str(row.get("explanation") or "")
            if assessment == "NO_DIFFERENCE_OBSERVED":
                explanation = (explanation + " Машинный просмотр не является подтверждённым "
                               "отсутствием нарушения.").strip()
            if selection_note:
                completeness = "CLARIFICATION_REQUIRED"
                if status == "NEGATIVE_VERIFIED":
                    status = None
                explanation = (
                    explanation + " Комплект содержит неразрешённую редакцию: "
                    + selection_note
                ).strip()
            raw_confidence = row.get("confidence")
            confidence = (
                float(raw_confidence)
                if isinstance(raw_confidence, int | float)
                and not isinstance(raw_confidence, bool)
                and 0 <= raw_confidence <= 1
                else None
            )
            checks.append({
                **_empty_check(parameter, completeness, explanation, "completed"),
                "finding_status": status,
                "expected_value": str(row.get("expected_value") or "") or None,
                "actual_value": str(row.get("actual_value") or "") or None,
                "evidence": evidence,
                "confidence": confidence,
                "delta": numeric_delta(row.get("expected_value"), row.get("actual_value")),
                "threshold_status": threshold_status(parameter, row.get("actual_value")),
                "approved_change_ref": str(row.get("approved_change_ref") or "NONE"),
                "stages_compared": sorted(stages),
                # Стадия, которой нет в комплекте, при парном сравнении
                # неприменима к этой проверке, а не «пропущена» (ТЗ 9.2).
                "stages_not_applicable": sorted(
                    stage for stage in ("RD", "ID") if stage not in selected),
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
        if selection_note and graphic_analysis["status"] == "completed":
            graphic_analysis["status"] = "incomplete"
            graphic_analysis["reason"] = (
                str(graphic_analysis.get("reason") or "")
                + " Комплект содержит неразрешённую редакцию: " + selection_note
            ).strip()
    elif cancelled and cancelled():
        graphic_analysis["reason"] = "графическая проверка остановлена инспектором"
    if progress:
        progress("Формирование протокола", min(len(checks), total), total)
    result = _with_document_selection(_result(object_id, checks), selected, problems)
    result["graphic_analysis"] = graphic_analysis
    result["ocr_quality"] = ocr_summary
    return result


def _result(object_id: str, checks: list[dict]) -> dict:
    completed = sum(item["technical_status"] == "completed" for item in checks)
    return {
        "matrix_version": current_matrix_version(), "object_id": object_id, "checks": checks,
        "coverage": {"total": len(checks), "completed": completed,
                     "not_run": len(checks) - completed},
        "graphic_analysis": {
            "status": "not_run",
            "reason": "графическая проверка не выполнялась",
            "candidates": [],
            "performance": {},
        },
    }


def clarification_result(object_id: str, reason: str, *, include_medium: bool = False) -> dict:
    """Результат, где проверка не начиналась: каждый параметр — уточнение.

    Нужен, когда вход не позволяет выбрать сопоставимые редакции вовсе
    (например, пакет без реестра файлов): отчёт по каждому параметру
    говорит, почему сравнения не было, а не выглядит пустым.
    """
    parameters = list_parameters()
    checks = [_empty_check(item, "CLARIFICATION_REQUIRED", reason) for item in parameters]
    result = _result(object_id, checks)
    result["document_selection"] = {"selected": {}, "problems": {"ALL": reason}}
    return result
