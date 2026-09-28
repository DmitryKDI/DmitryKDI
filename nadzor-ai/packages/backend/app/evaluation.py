"""Оценка результатов по эталонной разметке (ТЗ 14; лист «Метрики» матрицы).

На вход — эталон в полях листа «Схема GOLD» (по записи на evidence_group) и
номера процессов проверки тех же объектов; дополнительно — эталонный текст
страниц для OCR и эталонные ключевые поля. На выход — отчёт по каждой
метрике листа «Метрики»: значение, размер выборки, 95%-й доверительный
интервал, порог и итог, отдельно по разделам и параметрам.

Метрика, для которой нет данных, не считается пройденной: «не посчитано»
видно отдельно и роняет общий итог (ТЗ 14.3: «система не считается
принятой при недостижении любого обязательного порога»).

Эталон — данные заказчика: в репозиторий он не кладётся и в журнал не
пишется; сервис читает его только на время расчёта.
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from collections import defaultdict

from sqlalchemy.orm import Session

from . import facts_store, feedback, models, parameter_catalog
from .geometry import iou

# Порог совпадения рамки (ТЗ 14.3: «bbox/polygon считается верным при
# IoU ≥ 0,50»), z для 95%-го интервала, число повторов бутстрепа для F1.
IOU_THRESHOLD = 0.5
Z_95 = 1.96
BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 7

# Пороги листа «Метрики» (ТЗ 14.3) — значения из ТЗ.
THRESHOLDS = {
    "character_accuracy": (">=", 0.95),
    "key_fields_exact_match": (">=", 0.90),
    "document_linkage": (">=", 0.95),
    "localization": (">=", 0.95),
    "precision": (">=", feedback.ACCEPTANCE["precision"]),
    "recall": (">=", feedback.ACCEPTANCE["recall"]),
    "f1": (">=", feedback.ACCEPTANCE["f1"]),
    "false_positive_rate": ("<=", feedback.MAX_FALSE_POSITIVE_RATE),
}

POSITIVE, NEGATIVE = "CONFIRMED_VIOLATION", "NEGATIVE_VERIFIED"


def wilson(successes: int, total: int) -> list[float] | None:
    """95%-й интервал Уилсона для доли; None — выборка пуста."""
    if not total:
        return None
    p = successes / total
    denom = 1 + Z_95 ** 2 / total
    centre = (p + Z_95 ** 2 / (2 * total)) / denom
    half = Z_95 * math.sqrt(p * (1 - p) / total + Z_95 ** 2 / (4 * total ** 2)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def _levenshtein(a: list | str, b: list | str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def _normalize(text: str) -> str:
    """NFC и схлопнутые пробелы (ТЗ 14.3: «Unicode NFC и нормализация повторных пробелов»)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()


def _boxes(value) -> list[list[float]]:
    """Рамки из поля эталона: одна рамка, список рамок или многоугольник."""
    if not value:
        return []
    if isinstance(value, list | tuple) and value and isinstance(value[0], int | float):
        numbers = [float(v) for v in value]
        if len(numbers) == 4:
            return [numbers]
        xs, ys = numbers[0::2], numbers[1::2]
        return [[min(xs), min(ys), max(xs), max(ys)]]
    return [box for item in value for box in _boxes(item)]


def _metric(name: str, successes: int, total: int, **extra) -> dict:
    value = successes / total if total else None
    op, threshold = THRESHOLDS[name]
    passed = None if value is None else (value >= threshold if op == ">=" else value <= threshold)
    return {"value": None if value is None else round(value, 4), "size": total,
            "ci95": wilson(successes, total), "threshold": f"{op} {threshold}",
            "passed": passed, **extra}


# --- система: что сервис выдал по процессам ------------------------------------

def _system(db: Session, process_ids: list[int]) -> dict:
    findings: dict[tuple[str, str], list[dict]] = defaultdict(list)
    selected: dict[str, list[dict]] = defaultdict(list)
    files: dict[str, dict] = {}
    for process_id in process_ids:
        run = db.get(models.OfficialRun, process_id)
        if run is None or not run.result:
            continue
        documents = {item["id"]: item for item in run.input_snapshot or []}
        chosen = {i for ids in ((run.result.get("document_selection") or {})
                                .get("selected") or {}).values() for i in ids}
        for document_id in chosen:
            if document_id in documents:
                selected[run.object_id].append(documents[document_id]["metadata"])
        for item in documents.values():
            file_id = item["metadata"].get("file_id")
            if file_id:
                files[file_id] = {"document_id": item["id"], **item["metadata"]}
        checks = list(run.result.get("checks") or [])
        checks += list((run.result.get("graphic_analysis") or {}).get("candidates") or [])
        for check in checks:
            findings[(run.object_id, check.get("parameter_code") or "")].append(check)
    return {"findings": findings, "selected": selected, "files": files}


def _evidence_hits(source_file: str | None, source_page, boxes: list, evidence: list[dict]) -> bool:
    for item in evidence:
        if str(item.get("file_id")) != str(source_file) or item.get("page") != source_page:
            continue
        if not boxes:
            return True  # эталон без рамки проверяет только файл и страницу
        if any(iou(item.get("bbox"), box) >= IOU_THRESHOLD for box in boxes):
            return True
    return False


def _localized(group: dict, evidence: list[dict]) -> bool:
    return all(_evidence_hits(group.get(f"{side}_file_id"), group.get(f"{side}_page"),
                              _boxes(group.get(f"{side}_bbox_polygon")), evidence)
               for side in ("source_expected", "source_actual")
               if group.get(f"{side}_file_id"))


def _linked(group: dict, selected: list[dict]) -> bool:
    for side in ("source_expected", "source_actual"):
        code, revision = group.get(f"{side}_code"), group.get(f"{side}_revision")
        if not code:
            continue
        if not any(_normalize(meta.get("document_code")) == _normalize(code)
                   and (revision is None or _normalize(meta.get("revision")) == _normalize(revision))
                   for meta in selected):
            return False
    return True


# --- метрики --------------------------------------------------------------------

def _classification(groups: list[dict], system: dict) -> dict:
    """P/R/F1/FPR по evidence_group: верный параметр и верное доказательство."""
    outcomes = []
    abstained = 0
    for group in groups:
        label = group.get("finding_status")
        if label not in {POSITIVE, NEGATIVE}:
            continue
        key = (group.get("object_id"), group.get("matrix_code") or "")
        found = system["findings"].get(key, [])
        candidates = [c for c in found if c.get("finding_status") == "CANDIDATE"]
        if not found or all(c.get("technical_status") != "completed" for c in found):
            abstained += 1
        predicted = bool(candidates)
        correct = predicted and any(_localized(group, c.get("evidence") or []) for c in candidates)
        outcomes.append((group, label, predicted, correct))
    return {"outcomes": outcomes, "abstained": abstained}


def _prf(outcomes: list) -> dict:
    tp = sum(1 for _, label, _, correct in outcomes if label == POSITIVE and correct)
    fn = sum(1 for _, label, _, correct in outcomes if label == POSITIVE and not correct)
    fp = sum(1 for _, label, predicted, _ in outcomes if label == NEGATIVE and predicted)
    tn = sum(1 for _, label, predicted, _ in outcomes if label == NEGATIVE and not predicted)
    return {"tp": tp, "fn": fn, "fp": fp, "tn": tn}


def _f1(counts: dict) -> float | None:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    return 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else None


def _f1_interval(outcomes: list) -> list[float] | None:
    if not outcomes:
        return None
    rng = random.Random(BOOTSTRAP_SEED)  # noqa: S311 — бутстреп, не криптография
    values = []
    for _ in range(BOOTSTRAP_ROUNDS):
        sample = [outcomes[rng.randrange(len(outcomes))] for _ in outcomes]
        value = _f1(_prf(sample))
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return [round(values[int(0.025 * (len(values) - 1))], 4),
            round(values[int(0.975 * (len(values) - 1))], 4)]


def _detection(outcomes: list) -> dict:
    counts = _prf(outcomes)
    f1 = _f1(counts)
    op, threshold = THRESHOLDS["f1"]
    return {
        "counts": counts,
        "precision": _metric("precision", counts["tp"], counts["tp"] + counts["fp"]),
        "recall": _metric("recall", counts["tp"], counts["tp"] + counts["fn"]),
        "f1": {"value": None if f1 is None else round(f1, 4), "size": len(outcomes),
               "ci95": _f1_interval(outcomes), "threshold": f"{op} {threshold}",
               "passed": None if f1 is None else f1 >= threshold},
        "false_positive_rate": _metric("false_positive_rate", counts["fp"],
                                       counts["fp"] + counts["tn"]),
    }


def _ocr(db: Session, pages: list[dict], files: dict) -> dict:
    """Character Accuracy = 1 − ΣLevenshtein / Σсимволов; CER, WER, coverage."""
    distance = characters = word_distance = words = covered = 0
    for item in pages:
        reference = _normalize(item.get("text"))
        meta = files.get(str(item.get("file_id")))
        system_text = ""
        if meta is not None:
            document = db.get(models.Document, meta["document_id"])
            if document is not None:
                facts = facts_store.facts_for(document.file_path, document.name,
                                              digest=document.digest)
                system_text = _normalize(next((fact.get("text") for fact in facts.text_facts
                                               if fact.get("page") == item.get("page")), ""))
        covered += bool(system_text)
        distance += _levenshtein(reference, system_text)
        characters += len(reference)
        word_distance += _levenshtein(reference.split(), system_text.split())
        words += len(reference.split())
    metric = _metric("character_accuracy", max(0, characters - distance), characters)
    metric["cer"] = round(distance / characters, 4) if characters else None
    metric["wer"] = round(word_distance / words, 4) if words else None
    metric["coverage"] = round(covered / len(pages), 4) if pages else None
    metric["pages"] = len(pages)
    return metric


_FIELD_MAP = {"code": "document_code", "document_code": "document_code", "stage": "stage",
              "revision": "revision", "discipline": "discipline",
              "sheet": "sheet_page_range", "sheet_page_range": "sheet_page_range"}


def _key_fields(items: list[dict], files: dict) -> dict:
    """Exact Match ключевых полей после нормализации пробелов (знаки не удаляются)."""
    matched = unsupported = 0
    for item in items:
        meta = files.get(str(item.get("file_id"))) or {}
        field = _FIELD_MAP.get(str(item.get("field")))
        if field is None:
            unsupported += 1  # поле, которое сервис не извлекает, — промах, а не пропуск
            continue
        matched += _normalize(meta.get(field)) == _normalize(item.get("value"))
    return _metric("key_fields_exact_match", matched, len(items),
                   not_extracted_fields=unsupported)


def evaluate(db: Session, reference: dict | list, process_ids: list[int]) -> dict:
    """Отчёт по метрикам листа «Метрики» для процессов по эталону."""
    if isinstance(reference, list):
        reference = {"groups": reference}
    groups = [g for g in reference.get("groups") or [] if isinstance(g, dict)]
    system = _system(db, process_ids)

    classification = _classification(groups, system)
    outcomes = classification["outcomes"]
    detection = _detection(outcomes)

    complete = [g for g in groups if g.get("finding_status") in {POSITIVE, NEGATIVE}
                and _boxes(g.get("source_expected_bbox_polygon"))
                and _boxes(g.get("source_actual_bbox_polygon"))]
    localized = sum(1 for g in complete if any(
        _localized(g, c.get("evidence") or [])
        for c in system["findings"].get((g.get("object_id"), g.get("matrix_code") or ""), [])))
    linked = sum(1 for g in groups if _linked(g, system["selected"].get(g.get("object_id"), [])))

    sections = {item["code"]: item["section"]
                for item in parameter_catalog.list_parameters(include_inactive=True)}
    by_section: dict[str, list] = defaultdict(list)
    by_type: dict[str, list] = defaultdict(list)
    for outcome in outcomes:
        code = outcome[0].get("matrix_code") or "без параметра"
        by_section[sections.get(code, "без раздела")].append(outcome)
        by_type[code].append(outcome)

    metrics = {
        "character_accuracy": _ocr(db, reference.get("ocr") or [], system["files"]),
        "key_fields_exact_match": _key_fields(reference.get("key_fields") or [],
                                              system["files"]),
        "document_linkage": _metric("document_linkage", linked, len(groups)),
        "localization": _metric("localization", localized, len(complete)),
        **{key: value for key, value in detection.items() if key != "counts"},
    }
    failed = [name for name, value in metrics.items() if value["passed"] is False]
    missing = [name for name, value in metrics.items() if value["passed"] is None]
    return {
        "groups": len(groups), "process_ids": process_ids,
        "coverage": {"evaluated": len(outcomes), "abstained": classification["abstained"],
                     "abstention_rate": round(classification["abstained"] / len(outcomes), 4)
                     if outcomes else None},
        "counts": detection["counts"], "metrics": metrics,
        "by_section": {name: _detection(items) for name, items in sorted(by_section.items())},
        "by_violation_type": {name: _detection(items) for name, items in sorted(by_type.items())},
        "passed": not failed and not missing, "failed": failed, "not_computed": missing,
    }
