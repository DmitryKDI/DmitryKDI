#!/usr/bin/env python3
"""Консольное сравнение ПД/РД без запуска сервера и фронтенда.

Тот же путь, что и `_run_analysis` в `app/main.py` (extract → match → LLM
по каждой паре), но без базы и без FastAPI — только stdout. Для быстрой
проверки того, что реально отвечает LLM (в т.ч. local/Ollama), без клика
по интерфейсу.

Запуск:
  python scripts/scan_cli.py --before ПД.pdf --after РД1.pdf --after РД2.pdf
  python scripts/scan_cli.py --before ПД.pdf --after РД.pdf --provider local --model qwen2.5vl:7b
  python scripts/scan_cli.py --before ПД.pdf --after РД.pdf --pairs-limit 5   # только первые 5 пар, для быстрой проверки
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.classification import classify_document  # noqa: E402
from app.documents import extract_document_facts  # noqa: E402
from app.llm import LlmConfig  # noqa: E402
from app.matching import DocumentInput, match_page_pairs  # noqa: E402
from app.vision import compare_page_pair, compare_text_pair, make_llm_stamp_classifier  # noqa: E402


def _load_side(paths: list[str], config: LlmConfig):
    docs, inputs = [], []
    vision_fn = make_llm_stamp_classifier(config)
    for path in paths:
        name = Path(path).name
        facts = extract_document_facts(path, name)
        classification = classify_document(path, name, vision_stamp_fn=vision_fn)
        docs.append({"path": path, "name": name, "discipline_code": classification.discipline_code,
                     "facts": facts})
        inputs.append(DocumentInput(name, facts.pages, facts.text_facts, facts.room_facts,
                                    classification.discipline_code, facts.page_kinds,
                                    facts.equipment_facts))
        print(f"  {name}: {facts.pages} стр., раздел={classification.discipline_code or '?'}, "
              f"исключено {len(facts.excluded)} листов")
    return docs, inputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Консольное сравнение ПД/РД")
    parser.add_argument("--before", action="append", required=True, help="PDF стороны ПД (можно несколько раз)")
    parser.add_argument("--after", action="append", required=True, help="PDF стороны РД/ИД (можно несколько раз)")
    parser.add_argument("--provider", default="gigachat",
                        choices=["openai", "anthropic", "google", "yandexgpt", "gigachat"])
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--pairs-limit", type=int, default=0, help="Разобрать только первые N пар (0 = все)")
    parser.add_argument("--timeout", type=float, default=120.0, help="Таймаут одного вызова LLM, секунд")
    parser.add_argument("--json", dest="json_path", default="", help="Сохранить весь результат в файл")
    args = parser.parse_args()

    config = LlmConfig(provider=args.provider, api_key=args.api_key, base_url=args.base_url, model=args.model)
    print(f"Провайдер: {config.provider}  модель: {config.resolved_model()}  "
          f"эндпоинт: {config.resolved_base_url() or '(по умолчанию)'}\n")

    print("ПД:")
    before_docs, before_inputs = _load_side(args.before, config)
    print("РД/ИД:")
    after_docs, after_inputs = _load_side(args.after, config)

    pairs = match_page_pairs(before_inputs, after_inputs)
    print(f"\nСопоставлено пар: {len(pairs)}")
    if args.pairs_limit:
        pairs = pairs[: args.pairs_limit]
        print(f"(ограничено первыми {len(pairs)} для быстрой проверки)")

    def _page_text(docs, idx, page):
        return "\n".join(f["text"] for f in docs[idx]["facts"].text_facts if f["page"] == page)

    results = []
    for i, p in enumerate(pairs, 1):
        b, a = before_docs[p.before_file_idx], after_docs[p.after_file_idx]
        context = f"раздел {b['discipline_code'] or '?'}"
        print(f"\n[{i}/{len(pairs)}] {b['name']} стр.{p.before_page}  <->  "
              f"{a['name']} стр.{p.after_page}  (скор={p.score:.3f}, {p.matched_by}, {p.page_kind})")
        try:
            if p.page_kind == "text":
                result = compare_text_pair(
                    _page_text(before_docs, p.before_file_idx, p.before_page),
                    _page_text(after_docs, p.after_file_idx, p.after_page),
                    config, context=context, discipline=b["discipline_code"], timeout=args.timeout)
            else:
                result = compare_page_pair(b["path"], p.before_page, a["path"], p.after_page,
                                           config, context=context, discipline=b["discipline_code"],
                                           timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна ронять весь прогон
            print(f"  ОШИБКА: {exc}")
            results.append({"pair": i, "error": str(exc)})
            continue

        if result is None:
            print("  ИИ ответил, но ответ не разобран как JSON")
            results.append({"pair": i, "error": "unparsed"})
            continue

        significant = result.get("significant") or []
        if significant:
            for f in significant:
                print(f"  [{f.get('severity', '?')}] {f.get('change', '')}")
                if f.get("field_check"):
                    print(f"      → {f['field_check']}")
        else:
            print("  без значимых расхождений")
        results.append({"pair": i, "before": f"{b['name']}#{p.before_page}",
                        "after": f"{a['name']}#{p.after_page}", "result": result})

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nПолный результат сохранён: {args.json_path}")


if __name__ == "__main__":
    main()
