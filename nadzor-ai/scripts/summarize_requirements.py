#!/usr/bin/env python3
"""СТАДИЯ 1 — разбор проектной документации. Самодостаточна: рабочей
документации (РД) не требует и о ней ничего не знает.

Г.86 — перестройка по прямым указаниям пользователя. Раньше вся программа
была построена как «сравнение ПД↔РД», а извлечение из ПД шло приложением к
нему. Теперь наоборот: разбор ПД — основной продукт, сверка с РД — отдельная
надстройка (`scripts/compare_with_rd.py`), которая берёт готовый результат
этой стадии. **Отсутствие РД не режим ошибки, а норма** — её нет и не будет
на половине документов.

Порядок шагов задан пользователем и меняется только по новому прямому
указанию:

  1. комплект — какие файлы, какие разделы;
  2. факты   — текст страниц;
  3. требования и способы производства работ — ЛЛМ читает текст и сразу
     отдаёт готовый список с разделом и страницей;
  4. сводка  — то, что видит инспектор;
  5. реестры — помещения, оборудование, спецификации, таблицы;
  6. графика — что есть на чертежах.

Шаг 3 — главный. ЛЛМ читает текст напрямую (прямое решение пользователя:
«можно просто весь текст целиком отправлять в ЛЛМ, чтобы она сразу делала
сводку»), поэтому отдельного прохода на отсев шума больше нет: промпт сам
отделяет требование от декларации. Цена известна и принята — на томе в 177
страниц это ~69 вызовов против ~5 у regex-пути; взамен исчезает слепое
пятно регулярки (реальный пропуск Г.56). Без ключа ЛЛМ шаг 3 деградирует
на regex-каталог: сводка выходит сырой, но программа не встаёт.

Запуск:
    python scripts/summarize_requirements.py --pd том.pdf [--pd том2.pdf ...] \
        --provider gigachat --api-key ВАШ_КЛЮЧ [--out summary.txt]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "backend"))

from app.classification import classify_document, open_pdf  # noqa: E402
from app.document_composition import (  # noqa: E402
    describe_volume,
    name_unread_sheets,
    render_composition,
)

# Г.94 — рендер сводки и предполётная проверка живут в пакете приложения:
# их же вызывает HTTP-эндпоинт разбора ПД. Здесь только обвязка CLI.
from app.llm import LlmConfig, check_llm_reachable  # noqa: E402
from app.pd_stage import attach_norms, render_summary  # noqa: E402
from app.pd_store import save_run  # noqa: E402
from app.requirement_registry import extract_general_requirements  # noqa: E402
from app.set_overview import official_section_label  # noqa: E402
from app.stamp_vision import read_stamp_ocr  # noqa: E402
from registry_diff import (  # noqa: E402
    _PROVIDER_ENV_KEY,
    _extract_requirements_llm_visible,
    _load_text_facts,
)


def _identity_line(path: str) -> str:
    """Одна строка «что это за файл» — раздел и число страниц."""
    classification = classify_document(path, Path(path).name)
    label = official_section_label(classification.discipline_code)
    code = f" [{classification.discipline_code}]" if classification.discipline_code else ""
    try:
        doc = open_pdf(path)
        try:
            pages = doc.page_count
        finally:
            doc.close()
    except Exception:  # noqa: BLE001 — не смогли открыть, но имя файла показать всё равно надо
        pages = "?"
    return f"  {Path(path).name} — {label}{code} ({classification.source}), {pages} стр."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pd", action="append", required=True, help="Файл(ы) проектной документации")
    parser.add_argument("--provider", default="gigachat", choices=["anthropic", "gigachat"])
    parser.add_argument(
        "--api-key", default="",
        help="Ключ провайдера. Если не передан, берётся из переменной окружения по провайдеру "
             f"({', '.join(_PROVIDER_ENV_KEY.values())}). Без ключа работает regex-путь: "
             "сводка выходит сырой, но прогон не падает.",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--out", default="", help="Дублировать вывод в файл по мере готовности (Г.41)")
    parser.add_argument(
        "--sheet-name-vision", type=int, default=0, metavar="N",
        help="Дочитать наименования листов по изображению штампа, не более N "
             "вызовов на весь прогон (Г.99). "
             "Один вызов модели на ЛИСТ, поэтому по умолчанию выключено: у "
             "рабочей документации это порядка сотни вызовов на том.",
    )
    parser.add_argument(
        "--check-llm", action="store_true",
        help="Только проверить связь с провайдером и выйти. Г.91: разбор тома — десятки "
             "вызовов, а при обрыве связи каждый молча даёт пустой результат.",
    )
    args = parser.parse_args()

    # Г.82 — несуществующий путь раньше давал чистый отчёт из нулей с
    # exit=0, неотличимый от «в документе нет требований».
    missing = [p for p in args.pd if not Path(p).is_file()]
    if missing:
        sys.exit("ОШИБКА: файл(ы) --pd не найдены, отчёт НЕ построен (не путать с "
                 "«в документе нет требований»):\n" + "\n".join(f"  {p}" for p in missing))

    api_key = args.api_key or os.environ.get(_PROVIDER_ENV_KEY.get(args.provider, ""), "")
    llm_config = (
        LlmConfig(provider=args.provider, api_key=api_key, base_url=args.base_url, model=args.model)
        if api_key else None
    )

    if args.check_llm:
        ok, message = check_llm_reachable(llm_config)
        print(f"Проверка связи [{args.provider}]: {message}")
        sys.exit(0 if ok else 2)

    # noqa: SIM115 — файл живёт весь прогон и закрывается в finally: вывод
    # пишется по мере готовности (Г.41), а не одним куском в конце.
    out_f = open(args.out, "a", encoding="utf-8") if args.out else None  # noqa: SIM115

    def _emit(text: str) -> None:
        print(text)
        if out_f:
            out_f.write(text + "\n")
            out_f.flush()

    try:
        # Шаг 1 — комплект.
        _emit("=== Шаг 1. Комплект ===")
        for path in args.pd:
            _emit(_identity_line(path))
        # Г.95/Г.98 — состав считается ВСЕГДА и без модели: сводка обязана
        # покрывать весь документ, а не только его текстовую часть, и должна
        # получаться даже когда ключа нет. Тот же код, что у сервера (Г.94).
        volumes = [describe_volume(path, Path(path).name) for path in args.pd]
        _emit(render_composition(volumes))
        _emit("")

        # Шаг 2 — факты (текст страниц с разделом и именем файла, Г.86).
        _emit("=== Шаг 2. Текст ===")
        pd_text_facts = _load_text_facts(args.pd)
        _emit(f"  страниц с текстом: {len(pd_text_facts)}")
        _emit("")

        # Шаг 3 — требования. Главный шаг.
        _emit("=== Шаг 3. Требования и способы производства работ ===")
        # Г.103 — перечень нормативов приходит попутно с выжимкой.
        llm_norms: list[dict] = []
        if llm_config is not None:
            # Г.91 — связь проверяется ДО десятков вызовов, а не по их итогу.
            reachable, why = check_llm_reachable(llm_config)
            if not reachable:
                sys.exit(f"ОШИБКА: связь с провайдером {args.provider} не прошла — {why}\n"
                         "  Разбор НЕ выполнен. Это не «в документе нет требований» (Г.10/Г.77).\n"
                         "  Без ключа осознанно: запустите без --api-key — будет regex-путь.")
            # Г.99 — наименования листов, не давшиеся текстом, дочитываются
            # по изображению штампа. Только после проверки связи и только по
            # явному бюджету: шаг стоит вызов на лист.
            if args.sheet_name_vision > 0:
                remaining = args.sheet_name_vision
                for volume, path in zip(volumes, args.pd, strict=True):
                    if remaining <= 0:
                        break  # бюджет один на прогон, а не на каждый том
                    name_unread_sheets(
                        volume, path,
                        lambda page: read_stamp_ocr(page, llm_config).sheet_name,
                        remaining)
                    remaining -= volume.vision_calls
                _emit("  наименования листов дочитаны по изображению:")
                _emit(render_composition(volumes))
            requirements = _extract_requirements_llm_visible(
                pd_text_facts, llm_config, _emit, on_norms=llm_norms.extend)
        else:
            _emit("  Ключ ЛЛМ не задан — regex-путь: сводка будет СЫРОЙ, с шумом.")
            _emit("  Это не ошибка, но результат хуже: чтобы модель отсеяла шум сама,")
            _emit("  передайте --api-key или задайте переменную окружения.")
            requirements = extract_general_requirements(pd_text_facts)
            for req in requirements:  # Г.86: regex-путь не знает про раздел, проставляем из страниц
                for fact in pd_text_facts:
                    if fact.get("page") == req.page:
                        req.document = fact.get("document", "")
                        req.section = fact.get("section")
                        break
        _emit(f"  извлечено: {len(requirements)}")
        _emit("")

        # Шаг 4 — нормативная база: к какому документу перечня относится
        # параметр (Г.101). Считается ДО сводки, потому что сводка печатает
        # привязку рядом с требованием.
        _emit("=== Шаг 4. Нормативная база ===")
        _, norms_section = attach_norms(requirements, pd_text_facts, llm_config, llm_norms)
        _emit(norms_section)
        _emit("")

        # Шаг 5 — сводка: то, ради чего всё запускалось.
        _emit("=== Шаг 5. Сводка для инспектора ===")
        _emit(render_summary(requirements))
        _emit("")

        # Г.87 — результат сохраняется: отсюда его берёт стадия 2 (сверка с
        # РД), не переизвлекая ПД заново, и здесь же копится датасет.
        run_id = save_run(
            requirements,
            documents=[Path(p).name for p in args.pd],
            extractor="llm" if llm_config is not None else "regex",
            provider=args.provider if llm_config is not None else "",
            model=args.model if llm_config is not None else "",
        )
        _emit(f"=== Сохранено: прогон №{run_id} ===")
        _emit(f"  Сверка с РД по этому разбору: python scripts/compare_with_rd.py --run {run_id} --rd <файл РД>")
        _emit("")

        # Шаги 6-7 — реестры и графика. Пока не подключены к этой стадии:
        # Г.10 требует сказать об этом явно, а не молчать.
        _emit("=== Шаги 6-7. Реестры и графика — пока не подключены к этой стадии ===")
        _emit("  Реестры помещений/оборудования, спецификации, таблицы и разбор чертежей")
        _emit("  в движке есть и работают, но к стадии разбора ПД ещё не подключены —")
        _emit("  они писались для сверки с РД. Подключение — отдельный шаг.")
    finally:
        if out_f:
            out_f.close()


if __name__ == "__main__":
    main()
