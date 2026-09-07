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
from app.llm import LlmConfig, call_llm_json  # noqa: E402
from app.pd_store import save_run  # noqa: E402
from app.requirement_registry import (  # noqa: E402
    Requirement,
    _normalize_for_dedup,
    extract_general_requirements,
)
from app.set_overview import official_section_label  # noqa: E402
from registry_diff import (  # noqa: E402
    _PROVIDER_ENV_KEY,
    _extract_requirements_llm_visible,
    _load_text_facts,
)


def check_llm_reachable(llm_config) -> tuple[bool, str]:
    """Предполётная проверка связи с провайдером — один короткий вызов.

    Г.91. Разбор тома — это десятки вызовов и минуты работы. Если связи
    нет, каждый вызов молча возвращает пустой результат, и прогон
    заканчивается правдоподобным отчётом, в котором ничего не найдено, —
    ровно ловушка Г.77: 100%-ный отказ связи был неотличим от «модель со
    всем согласна» и стоил трёх раундов правок промпта против бага,
    которого в промпте не было. Один вызов до начала работы отделяет
    «связи нет» от «модель так ответила».

    Три состояния различаются явно, а не сводятся к «не получилось»:
    ключа нет (проверять нечего), связь есть, связь не прошла — с точной
    причиной, потому что «сеть не пускает» и «ключ протух» требуют от
    пользователя разных действий (Г.10).
    """
    if llm_config is None:
        return False, "ключ ЛЛМ не задан — проверять нечего"
    try:
        call_llm_json(
            llm_config,
            "Ты отвечаешь строго JSON. Проверка связи.",
            'Ответь ровно: {"ok": true}',
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 — причина нужна целиком, любая
        return False, f"{type(exc).__name__}: {exc}"
    return True, "связь с провайдером есть"


def _group_repeated(items: list[Requirement]) -> list[list[Requirement]]:
    """Одинаковые по тексту требования — в одну группу, порядок первого
    появления сохраняется. Ключ тот же, что у каталога формы 3
    (`_normalize_for_dedup`), чтобы «одинаковость» означала одно и то же в
    обоих отчётах, а не два похожих правила в разных местах."""
    groups: dict[str, list[Requirement]] = {}
    order: list[str] = []
    for req in items:
        key = _normalize_for_dedup(req.summary or req.sentence)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(req)
    return [groups[k] for k in order]


def render_summary(requirements: list[Requirement]) -> str:
    """Сводка для инспектора: раздел, страница, текст требования.

    Г.86 — прямое требование пользователя: «требования должны быть уже
    привязаны к разделам... чтобы инспектор просто увидел результат, на
    какой странице требование». Поэтому группировка по разделу (а внутри —
    по документу и странице), а не сплошной список: на комплекте из
    нескольких томов сплошной список нечитаем, и непонятно, к чему
    относится требование.

    Г.90 — одинаковая формулировка с разных страниц печатается ОДНОЙ
    строкой со списком всех страниц. Примечание, повторённое на каждом
    листе многостраничной таблицы, иначе занимает столько строк, сколько
    листов в таблице: на реальном томе это 88 строк против 69 уникальных
    формулировок. Ни одна страница при этом не теряется и число повторов
    показано явно — сжимается вид, а не данные (Г.10). Дедупликация НЕ
    переходит границу документа: одна и та же фраза в двух томах — два
    разных факта, инспектору важно, в каком томе искать.
    """
    if not requirements:
        return "Требований не извлечено."

    by_section: dict[tuple[str, str], list[Requirement]] = {}
    for req in requirements:
        by_section.setdefault((req.section or "", req.document or ""), []).append(req)

    out: list[str] = []
    for (section, document), items in sorted(by_section.items()):
        label = official_section_label(section) if section else "Раздел не определён"
        code = f" [{section}]" if section else ""
        groups = _group_repeated(items)
        unique_note = (f", уникальных формулировок: {len(groups)}"
                       if len(groups) != len(items) else "")
        out.append(f"\n=== {label}{code} — {document or 'файл не указан'} "
                   f"({len(items)} требований{unique_note}) ===")
        for group in sorted(groups, key=lambda g: g[0].page):
            first = group[0]
            pages = ", ".join(str(p) for p in sorted({r.page for r in group}))
            rooms_all = sorted({room for r in group for room in r.rooms},
                               key=lambda x: (len(x), x))
            rooms = f" (пом. {', '.join(rooms_all)})" if rooms_all else ""
            mark = f" [{first.code}]" if first.code else ""
            repeat = f" (повторено {len(group)}×)" if len(group) > 1 else ""
            out.append(f"  стр.{pages}{mark}{rooms}{repeat}")
            out.append(f"    {first.summary or first.sentence}")
    return "\n".join(out)


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
        _emit("")

        # Шаг 2 — факты (текст страниц с разделом и именем файла, Г.86).
        _emit("=== Шаг 2. Текст ===")
        pd_text_facts = _load_text_facts(args.pd)
        _emit(f"  страниц с текстом: {len(pd_text_facts)}")
        _emit("")

        # Шаг 3 — требования. Главный шаг.
        _emit("=== Шаг 3. Требования и способы производства работ ===")
        if llm_config is not None:
            # Г.91 — связь проверяется ДО десятков вызовов, а не по их итогу.
            reachable, why = check_llm_reachable(llm_config)
            if not reachable:
                sys.exit(f"ОШИБКА: связь с провайдером {args.provider} не прошла — {why}\n"
                         "  Разбор НЕ выполнен. Это не «в документе нет требований» (Г.10/Г.77).\n"
                         "  Без ключа осознанно: запустите без --api-key — будет regex-путь.")
            requirements = _extract_requirements_llm_visible(pd_text_facts, llm_config, _emit)
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

        # Шаг 4 — сводка: то, ради чего всё запускалось.
        _emit("=== Шаг 4. Сводка для инспектора ===")
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

        # Шаги 5-6 — реестры и графика. Пока не подключены к этой стадии:
        # Г.10 требует сказать об этом явно, а не молчать.
        _emit("=== Шаги 5-6. Реестры и графика — пока не подключены к этой стадии ===")
        _emit("  Реестры помещений/оборудования, спецификации, таблицы и разбор чертежей")
        _emit("  в движке есть и работают, но к стадии разбора ПД ещё не подключены —")
        _emit("  они писались для сверки с РД. Подключение — отдельный шаг.")
    finally:
        if out_f:
            out_f.close()


if __name__ == "__main__":
    main()
