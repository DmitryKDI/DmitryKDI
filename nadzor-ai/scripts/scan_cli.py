#!/usr/bin/env python3
"""Консольное сравнение ПД/РД с трёхуровневой маршрутизацией, без сервера
и фронтенда.

Пайплайн:
  1. Извлечение фактов (текст, помещения, оборудование)
  2. Сопоставление пар (matching)
  3. Детерминированный анализ (router.py):
     - Уровень 0: реестры совпали (или прайс поставщика) → пропуск
     - Уровень 2: расхождения в реестрах → LLM обязателен
     - Уровень 3: текстовый diff > порога → LLM условно
  4. Кросс-проверка помещений по всему комплекту (room_cross_check.py):
     номера ПД, отсутствующие в РД, независимо от того, попали ли они в
     какую-то сопоставленную пару листов
  5. (опционально) LLM только для выбранных пар

Запуск:
  # Только машинный анализ, без LLM
  python scripts/scan_cli.py --before ПД.pdf --after РД1.pdf --after РД2.pdf --no-llm

  # С LLM, автоматически на уровень 2 (обязательные пары)
  python scripts/scan_cli.py --before ПД.pdf --after РД.pdf --auto-llm 2

  # Полный ручной выбор пар для LLM
  python scripts/scan_cli.py --before ПД.pdf --after РД.pdf
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.classification import classify_document  # noqa: E402
from app.documents import extract_document_facts  # noqa: E402
from app.matching import DocumentInput, match_page_pairs  # noqa: E402
from app.equip_cross_check import cross_check_equipment, render_equip_cross_check_report  # noqa: E402
from app.room_cross_check import cross_check_rooms, render_cross_check_report  # noqa: E402
from app.router import classify_all_pairs, render_report  # noqa: E402


def _load_side(paths: list[str], vision_fn=None):
    """Загрузка PDF: извлечение фактов + определение раздела по штампу.

    `vision_fn` — классификатор штампа по картинке (см.
    `vision.make_llm_stamp_classifier`); без него (режим --no-llm)
    штамп читается только текстом — раздел может остаться не определён
    там, где штамп только в кривых, но извлечение фактов от этого не
    страдает."""
    docs, inputs = [], []
    for path in paths:
        name = Path(path).name
        facts = extract_document_facts(path, name)
        classification = classify_document(path, name, vision_stamp_fn=vision_fn)
        docs.append({"path": path, "name": name, "discipline_code": classification.discipline_code,
                     "facts": facts})
        inputs.append(DocumentInput(name, facts.pages, facts.text_facts, facts.room_facts,
                                    classification.discipline_code, facts.page_kinds,
                                    facts.equipment_facts, facts.balance_facts))
        print(f"  {name}: {facts.pages} стр., раздел={classification.discipline_code or '?'}, "
              f"исключено {len(facts.excluded)} листов")
    return docs, inputs


def _page_text(docs, idx, page):
    return "\n".join(f["text"] for f in docs[idx]["facts"].text_facts if f["page"] == page)


def _select_pairs_for_llm(verdicts, args):
    """Определить, какие пары отправлять в LLM — авто (--auto-llm) или
    интерактивным выбором по уровням/индексам."""
    if args.auto_llm:
        levels = [int(l.strip()) for l in args.auto_llm.split(",") if l.strip()]
        if 0 in levels:
            return list(verdicts)
        return [v for v in verdicts if v.level in levels]

    level2_indices = [i for i, v in enumerate(verdicts) if v.level == 2]
    level3_indices = [i for i, v in enumerate(verdicts) if v.level == 3]
    level0_indices = [i for i, v in enumerate(verdicts) if v.level == 0]

    print(f"\nПар для LLM: {len(level2_indices) + len(level3_indices)} "
          f"(уровень 2: {len(level2_indices)}, уровень 3: {len(level3_indices)})")
    print(f"Пар для пропуска: {len(level0_indices)} (уровень 0)")
    print("\nВведите индексы пар для LLM через запятую (0-based из отчёта выше).")
    print("Подсказки: '2' — только уровень 2, '2,3' — уровни 2+3, 'all' — все,")
    print("'skip 5,10' — все кроме 5 и 10, 'q' — выйти без LLM.")

    while True:
        raw = input("\nВыбор: ").strip()
        if raw.lower() in ("q", "quit", "exit"):
            return []
        if raw.lower() == "all":
            return list(verdicts)
        if raw.lower() == "2":
            return [v for v in verdicts if v.level == 2]
        if raw.lower() == "3":
            return [v for v in verdicts if v.level == 3]
        if raw.lower() == "2,3":
            return [v for v in verdicts if v.level in (2, 3)]

        selected: set[int] = set()
        skip: set[int] = set()
        for token in raw.replace(",", " ").split():
            if token.lower().startswith("skip"):
                skip.update(int(x) for x in token[4:].strip().split() if x.isdigit())
            elif "-" in token:
                start, end = token.split("-", 1)
                selected.update(range(int(start), int(end) + 1))
            elif token.isdigit():
                selected.add(int(token))

        if skip:
            return [v for i, v in enumerate(verdicts) if i not in skip]
        if selected:
            return [verdicts[i] for i in selected if 0 <= i < len(verdicts)]
        print("Не понял. Попробуйте: '2', 'all', '1,3,5', '2-5', 'skip 3,7'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Консольное сравнение ПД/РД с маршрутизацией")
    parser.add_argument("--before", action="append", required=True, help="PDF стороны ПД (можно несколько раз)")
    parser.add_argument("--after", action="append", required=True, help="PDF стороны РД/ИД (можно несколько раз)")
    parser.add_argument("--provider", default="gigachat",
                        choices=["openai", "anthropic", "google", "yandexgpt", "gigachat"])
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--pairs-limit", type=int, default=0, help="Разобрать только первые N пар (0 = все)")
    parser.add_argument("--timeout", type=float, default=120.0, help="Таймаут одного вызова LLM, секунд")
    parser.add_argument("--auto-llm", default="",
                        help="Авто-LLM без ручного выбора: '2' (только ур.2), '2,3', 'all', '0' (все)")
    parser.add_argument("--json", dest="json_path", default="", help="Сохранить весь результат в файл")
    parser.add_argument("--no-llm", action="store_true",
                        help="Только машинный анализ (router + room_cross_check), без LLM")
    args = parser.parse_args()

    config = None
    if args.no_llm:
        print("Режим: машинный анализ без LLM\n")
        print("ПД:")
        before_docs, before_inputs = _load_side(args.before)
        print("РД/ИД:")
        after_docs, after_inputs = _load_side(args.after)
    else:
        from app.llm import LlmConfig  # noqa
        from app.vision import compare_page_pair, compare_text_pair, make_llm_stamp_classifier  # noqa

        config = LlmConfig(provider=args.provider, api_key=args.api_key, base_url=args.base_url, model=args.model)
        print(f"Провайдер: {config.provider}  модель: {config.resolved_model()}  "
              f"эндпоинт: {config.resolved_base_url() or '(по умолчанию)'}\n")
        vision_fn = make_llm_stamp_classifier(config)
        print("ПД:")
        before_docs, before_inputs = _load_side(args.before, vision_fn)
        print("РД/ИД:")
        after_docs, after_inputs = _load_side(args.after, vision_fn)

    # === МАТЧИНГ ===
    pairs = match_page_pairs(before_inputs, after_inputs)
    print(f"\nСопоставлено пар: {len(pairs)}")
    if args.pairs_limit:
        pairs = pairs[: args.pairs_limit]
        print(f"(ограничено первыми {len(pairs)} для быстрой проверки)")

    # === ДЕТЕРМИНИРОВАННЫЙ АНАЛИЗ (router.py) ===
    print("\n=== ДЕТЕРМИНИРОВАННЫЙ АНАЛИЗ ===")
    verdicts = classify_all_pairs(pairs, before_inputs, after_inputs, before_docs, after_docs)
    print(render_report(verdicts))

    # === КРОСС-ПРОВЕРКА ПОМЕЩЕНИЙ ПО ВСЕМУ КОМПЛЕКТУ (room_cross_check.py) ===
    print("\n=== КРОСС-ПРОВЕРКА ПОМЕЩЕНИЙ (Г.9/Г.23) ===")
    cross_result = cross_check_rooms(before_inputs, after_inputs)
    print(render_cross_check_report(cross_result))

    if cross_result.missing_anchors:
        print(f"\n{'=' * 60}")
        print(f"НАЙДЕНО: {len(cross_result.missing_anchors)} номеров из ПД отсутствуют в РД")
        print(f"{'=' * 60}")
        for key in sorted(cross_result.missing_anchors):
            finding = next((f for f in cross_result.findings if f.room_key == key), None)
            print(f"  • {key} «{finding.room_name_pd if finding else '?'}»")

    # === КРОСС-ПРОВЕРКА ОБОРУДОВАНИЯ ПО ВСЕМУ КОМПЛЕКТУ (equip_cross_check.py) ===
    print("\n=== КРОСС-ПРОВЕРКА ОБОРУДОВАНИЯ (Г.20) ===")
    equip_result = cross_check_equipment(before_inputs, after_inputs)
    print(render_equip_cross_check_report(equip_result))

    if equip_result.missing_in_rd:
        print(f"\n{'=' * 60}")
        print(f"НАЙДЕНО: {len(equip_result.missing_in_rd)} позиций оборудования из ПД отсутствуют в РД")
        print(f"{'=' * 60}")
        for key in sorted(equip_result.missing_in_rd):
            finding = next((f for f in equip_result.findings if f.equip_key == key
                           and f.finding_type == "missing_in_rd"), None)
            print(f"  • {key} «{finding.equip_name_pd if finding else '?'}»")

    if args.no_llm:
        print(f"\n{'=' * 60}")
        print("Машинный анализ завершён. Для LLM-проверки уберите --no-llm")
        print(f"{'=' * 60}")
        return

    # === ВЫБОР ПАР ДЛЯ LLM ===
    to_llm = _select_pairs_for_llm(verdicts, args)
    if not to_llm:
        print("\nНикаких пар не выбрано для LLM. Закончено.")
        return

    print(f"\nОтправляем в LLM: {len(to_llm)} пар\n")

    results = []
    for i, v in enumerate(to_llm, 1):
        p = v.pair
        b, a = before_docs[p.before_file_idx], after_docs[p.after_file_idx]
        context = f"раздел {b['discipline_code'] or '?'}"

        print(f"[{i}/{len(to_llm)}] {v.reason}")
        print(f"  [{b['name']} стр.{p.before_page}]  <->  [{a['name']} стр.{p.after_page}]  "
              f"(скор={p.score:.3f}, {p.matched_by}, {p.page_kind})")

        try:
            if p.page_kind == "text":
                bt = _page_text(before_docs, p.before_file_idx, p.before_page)
                at = _page_text(after_docs, p.after_file_idx, p.after_page)
                result = compare_text_pair(bt, at, config, context=context,
                                           discipline=b["discipline_code"], timeout=args.timeout)
            else:
                result = compare_page_pair(b["path"], p.before_page, a["path"], p.after_page,
                                           config, context=context, discipline=b["discipline_code"],
                                           timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001 — одна упавшая пара не должна ронять весь прогон
            print(f"  ОШИБКА: {exc}")
            results.append({"pair": i, "level": v.level, "error": str(exc)})
            continue

        if result is None:
            print("  ИИ ответил, но ответ не разобран как JSON")
            results.append({"pair": i, "level": v.level, "error": "unparsed"})
            continue

        significant = result.get("significant") or []
        if significant:
            for f in significant:
                print(f"  [{f.get('severity', '?')}] {f.get('change', '')}")
                if f.get("field_check"):
                    print(f"      → {f['field_check']}")
        else:
            print("  без значимых расхождений")
        results.append({"pair": i, "level": v.level, "before": f"{b['name']}#{p.before_page}",
                        "after": f"{a['name']}#{p.after_page}", "result": result})

    if args.json_path:
        Path(args.json_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nПолный результат сохранён: {args.json_path}")


if __name__ == "__main__":
    main()
