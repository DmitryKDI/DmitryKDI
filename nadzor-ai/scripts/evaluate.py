#!/usr/bin/env python3
"""Метрики качества детектора по эталону data/ground_truth.json.

Считаются precision, recall и F1 — общие и по каждому классу расхождений
отдельно. Детектор о содержимом эталона не знает.

Запуск:  python scripts/evaluate.py [--json отчёт.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_demo import run_object  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def title_map() -> dict[str, str]:
    """Имя файла из эталона → название документа, как его видит система."""
    manifest = json.loads(
        (ROOT / "data/demo/generated/manifest.json").read_text(encoding="utf-8"))
    return {Path(item["file"]).name: item["title"] for item in manifest["documents"]}


def _matches(expected: dict, finding, titles: dict[str, str]) -> bool:
    """Совпадение находки с эталонной записью.

    Сверяются класс расхождения, переход и либо конструктивный элемент,
    либо состав доказательной базы.
    """
    if finding.code != expected["code"] or finding.transition != expected["transition"]:
        return False
    mark = (expected.get("element") or {}).get("mark", "")
    found_mark = (finding.structural_element or {}).get("mark", "")
    if mark and found_mark and mark not in ("—", "") and mark == found_mark:
        return True
    expected_titles = {titles.get(name, name) for name in expected.get("evidence_docs", [])}
    found_titles = {e.doc_title for e in finding.evidence}
    return bool(expected_titles & found_titles)


def _score(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3)}


async def evaluate() -> dict:
    truth = json.loads((ROOT / "data/ground_truth.json").read_text(encoding="utf-8"))["expected"]
    object_ids = sorted({item["object_id"] for item in truth})
    titles = title_map()

    found_all, matched_pairs = [], []
    unmatched_expected = list(truth)
    for object_id in object_ids:
        result = await run_object(object_id)
        for finding in result["findings"]:
            found_all.append((object_id, finding))
            hit = next((e for e in unmatched_expected
                        if e["object_id"] == object_id and _matches(e, finding, titles)), None)
            if hit:
                unmatched_expected.remove(hit)
                matched_pairs.append((hit, finding))

    matched_ids = {id(f) for _, f in matched_pairs}
    false_positives = [(o, f) for o, f in found_all if id(f) not in matched_ids]

    by_class: dict[str, dict] = {}
    for code in sorted({item["code"] for item in truth} | {f.code for _, f in found_all}):
        tp = sum(1 for e, _ in matched_pairs if e["code"] == code)
        fn = sum(1 for e in unmatched_expected if e["code"] == code)
        fp = sum(1 for _, f in false_positives if f.code == code)
        by_class[code] = _score(tp, fp, fn)

    overall = _score(len(matched_pairs), len(false_positives), len(unmatched_expected))
    return {
        "objects": object_ids,
        "expected_total": len(truth),
        "found_total": len(found_all),
        "overall": overall,
        "by_class": by_class,
        "missed": [{"id": e["id"], "code": e["code"], "title": e["title"]}
                   for e in unmatched_expected],
        "false_positives": [{"object": o, "code": f.code, "title": f.title}
                            for o, f in false_positives],
    }


def print_report(report: dict) -> None:
    o = report["overall"]
    print(f"Объектов: {len(report['objects'])}   эталонных расхождений: "
          f"{report['expected_total']}   найдено: {report['found_total']}")
    print(f"\nОбщие метрики:  precision {o['precision']:.3f}   recall {o['recall']:.3f}   "
          f"F1 {o['f1']:.3f}   (TP {o['tp']}, FP {o['fp']}, FN {o['fn']})\n")
    print(f"{'Класс':<8}{'TP':>4}{'FP':>4}{'FN':>4}{'precision':>11}{'recall':>9}{'F1':>7}")
    print("-" * 47)
    for code, m in report["by_class"].items():
        print(f"{code:<8}{m['tp']:>4}{m['fp']:>4}{m['fn']:>4}"
              f"{m['precision']:>11.3f}{m['recall']:>9.3f}{m['f1']:>7.3f}")
    if report["missed"]:
        print("\nНе найдено:")
        for item in report["missed"]:
            print(f"  {item['id']} {item['code']}  {item['title'][:80]}")
    if report["false_positives"]:
        print("\nЛожные срабатывания:")
        for item in report["false_positives"]:
            print(f"  {item['object']} {item['code']}  {item['title'][:80]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Метрики детектора по эталону")
    parser.add_argument("--json", dest="json_path", default="")
    args = parser.parse_args()
    report = asyncio.run(evaluate())
    print_report(report)
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        print(f"\nОтчёт сохранён: {args.json_path}")


if __name__ == "__main__":
    main()
