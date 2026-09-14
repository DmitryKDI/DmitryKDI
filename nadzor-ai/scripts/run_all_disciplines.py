#!/usr/bin/env python3
"""Прогон `--kind all` не по одной паре файлов, а по ВСЕМ разделам сразу.

До этого скрипта пользователю приходилось вручную указывать путь к
конкретному файлу ПД и конкретному файлу РД для одного раздела за раз
(`registry_diff.py --before X --after Y`) — при комплекте в 50+ файлов на
15+ разделов это и создавало впечатление «код умеет только один раздел».
На самом деле механика (Г.63) раздело-независима почти везде — не хватало
только входа, который сам находит, какие файлы к какому разделу
относятся, и прогоняет каждый раздел через ту же цепочку `--kind all`,
что registry_diff.py уже прогоняет для одной пары.

Классификация файла по разделу — ТА ЖЕ функция, что использует сам
registry_diff.py на каждом документе (`classification.classify_document`:
имя файла -> титульный лист -> штамп текстом -> штамп зрением), не новая
эвристика по имени папки — раскладка папок реального комплекта не
гарантированно совпадает с кодом раздела (Г.4).

Запуск (Windows, тот же ключ и провайдер, что уже использовались вручную):
    python scripts/run_all_disciplines.py ^
        --pd-root "C:\\OSR\\DmitryKDI\\123\\ПД" ^
        --rd-root "C:\\OSR\\DmitryKDI\\123\\Рабочая и исполнительная документация" ^
        --provider gigachat --api-key "ВАШ_КЛЮЧ" ^
        --out-dir out_by_discipline

Результат — по одному файлу `out_by_discipline/<КОД_РАЗДЕЛА>.txt` на
каждый найденный раздел, с тем же содержимым, что дал бы отдельный вызов
`registry_diff.py --kind all --verify-requirements` для этого раздела.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.classification import classify_document  # noqa: E402
from registry_diff import LlmConfig, run_requirements, run_triangulated  # noqa: E402


def _find_pdfs(root: str) -> list[str]:
    return sorted(str(p) for p in Path(root).rglob("*.pdf"))


def _classify_all(paths: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    by_discipline: dict[str, list[str]] = defaultdict(list)
    unclassified: list[str] = []
    for path in paths:
        result = classify_document(path, Path(path).name)
        if result.discipline_code:
            by_discipline[result.discipline_code].append(path)
        else:
            unclassified.append(path)
    return dict(by_discipline), unclassified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pd-root", required=True, help="Папка с ПД — ищет *.pdf рекурсивно во всех подпапках")
    parser.add_argument("--rd-root", required=True, help="Папка с РД/ИД (АОСР и т.п.) — та же рекурсивная логика поиска")
    parser.add_argument("--provider", default="gigachat", choices=["anthropic", "gigachat"])
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out-dir", default="out_by_discipline")
    parser.add_argument("--rooms", default="",
                         help="Список номеров помещений через запятую — тот же смысл, что у "
                              "registry_diff.py --rooms, применяется одинаково ко всем разделам сразу")
    args = parser.parse_args()

    llm_config = LlmConfig(provider=args.provider, api_key=args.api_key, base_url=args.base_url, model=args.model)

    pd_paths = _find_pdfs(args.pd_root)
    rd_paths = _find_pdfs(args.rd_root)
    if not pd_paths:
        print(f"!!! В {args.pd_root} не найдено ни одного .pdf — проверьте путь !!!", file=sys.stderr)
        return
    if not rd_paths:
        print(f"!!! В {args.rd_root} не найдено ни одного .pdf — проверьте путь !!!", file=sys.stderr)

    print(f"Найдено ПД: {len(pd_paths)} файл(ов), РД/ИД: {len(rd_paths)} файл(ов). Классифицирую по разделам "
          f"(имя файла -> титульный лист -> штамп; на файлах со штампом-картинкой без текстового слоя "
          f"классификация может не сработать без зрения — см. предупреждения ниже)...")
    pd_by_discipline, pd_unclassified = _classify_all(pd_paths)
    rd_by_discipline, rd_unclassified = _classify_all(rd_paths)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_disciplines = sorted(set(pd_by_discipline) | set(rd_by_discipline))
    print(f"\nРазделов определено: {len(all_disciplines)} — {', '.join(all_disciplines) or '(ни одного)'}")
    if pd_unclassified:
        print(f"\nПД не удалось классифицировать ({len(pd_unclassified)} файл(ов)) — раздел не определён, "
              f"в общий прогон НЕ попадут:")
        for p in pd_unclassified:
            print(f"  {p}")
    if rd_unclassified:
        print(f"\nРД/ИД не удалось классифицировать ({len(rd_unclassified)} файл(ов)) — раздел не определён, "
              f"в общий прогон НЕ попадут:")
        for p in rd_unclassified:
            print(f"  {p}")

    room_keys = [r.strip() for r in args.rooms.split(",") if r.strip()]

    summary: list[str] = []
    for code in all_disciplines:
        before = pd_by_discipline.get(code, [])
        after = rd_by_discipline.get(code, [])
        out_path = str(out_dir / f"{code}.txt")
        print(f"\n{'=' * 70}\nРаздел {code}: ПД {len(before)} файл(ов), РД/ИД {len(after)} файл(ов) -> {out_path}\n{'=' * 70}")
        try:
            if before and after:
                run_triangulated(before, after, room_keys, llm_config, out_path=out_path)
                summary.append(f"{code}: полный --kind all (ПД {len(before)}, РД/ИД {len(after)}) -> {out_path}")
            elif before and not after:
                run_requirements(before, [], llm_config, out_path=out_path)
                summary.append(f"{code}: только ПД, нет РД/ИД — прогнана ТОЛЬКО сводка требований "
                                f"(Г.32/47), не полный --kind all -> {out_path}")
            else:
                print(f"(раздел {code}: только РД/ИД без ПД — сравнивать не с чем, пропущено)")
                summary.append(f"{code}: только РД/ИД без ПД — пропущено, сравнивать не с чем")
        except Exception as exc:  # noqa: BLE001 — сбой одного раздела (сеть, битый файл, что угодно
            # непредвиденное) не должен стопорить весь комплект из 15+ разделов — это и есть
            # весь смысл этого скрипта (Г.73). Раздел помечается явно, не молчит (Г.10), прогон
            # продолжается со следующего раздела, уже посчитанное на диске (out_path) не теряется.
            print(f"!!! Раздел {code} упал с ошибкой, ПРОПУЩЕН, прогон продолжается: {exc!r} !!!", file=sys.stderr)
            summary.append(f"{code}: ОШИБКА, раздел не прогнан целиком — {exc!r} (см. stderr выше, "
                            f"частичный результат может быть в {out_path})")

    print(f"\n{'=' * 70}\nИТОГ ПО ВСЕМ РАЗДЕЛАМ ({len(all_disciplines)})\n{'=' * 70}")
    for line in summary:
        print(f"  {line}")
    if pd_unclassified or rd_unclassified:
        print(f"\nНе классифицировано: ПД {len(pd_unclassified)}, РД/ИД {len(rd_unclassified)} — "
              f"см. списки выше, эти файлы ни в один раздел не попали.")


if __name__ == "__main__":
    main()
