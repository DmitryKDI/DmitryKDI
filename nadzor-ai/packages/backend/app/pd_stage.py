"""Стадия 1 — разбор проектной документации: общая логика CLI и сервера.

Г.94. Раньше рендер сводки и загрузка текста страниц жили внутри
`scripts/summarize_requirements.py`, то есть были доступны только из
командной строки: сервер (`main.py`) их импортировать не может — `scripts`
не пакет приложения. Из-за этого инспектор через интерфейс не мог получить
сводку вообще, хотя весь движок был написан. Логика перенесена сюда, а CLI
и HTTP-эндпоинт стали двумя вызывающими одного кода — не двумя копиями,
которые разойдутся.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

from .classification import classify_document
from .documents import extract_document_facts
from .llm import LlmConfig
from .norms_registry import (
    find_norms,
    link_by_meaning,
    link_cited,
    norms_from_llm,
    render_norms_section,
)
from .requirement_registry import (
    Requirement,
    _normalize_for_dedup,
    match_requirement_rooms_by_name,
    topic_prefixes,
)
from .section_profile import (
    KIND_NORM,
    KIND_TABLE,
    KIND_TERM,
    collect_terms,
    observe,
    render_profile,
)
from .set_overview import official_section_label


def load_text_facts(sources: list[tuple]) -> list[dict]:
    """[{page, text, document, section}] по ВСЕМ страницам всех файлов.

    `sources` — пары «путь на диске, отображаемое имя»: на сервере файл
    лежит под сгенерированным именем (`uuid.pdf`), а инспектору и
    классификации нужно настоящее имя тома. Классификация делается один
    раз на файл, не на страницу: `classify_document` читает имя файла,
    титульный лист и штамп (Г.13/79/80).

    Намеренно БЕЗ фильтра `material.py`: он правильно исключает страницы
    каталога поставщика из реестров помещений и оборудования, но здесь
    нужен весь текст тома — требование может стоять на странице, подшитой
    внутрь коммерческого предложения (Г.34).

    Битый файл или неопределившийся раздел не роняют прогон: причина
    печатается, работа продолжается (Г.10 — пропуск виден, не молчит).
    """
    out: list[dict] = []
    for source in sources:
        path, display_name = source[0], source[1]
        manual_section = source[2] if len(source) > 2 else None
        p = Path(path)
        if not p.is_file():
            print(f"пропущен (не найден): {display_name}", file=sys.stderr)
            continue
        try:
            doc = pymupdf.open(str(p))
        except Exception as exc:  # noqa: BLE001 — один битый файл не роняет прогон
            print(f"пропущен ({exc}): {display_name}", file=sys.stderr)
            continue
        if manual_section:
            section = manual_section
        else:
            try:
                section = classify_document(str(p), display_name).discipline_code
            except Exception as exc:  # noqa: BLE001 — раздел не определён, текст всё равно нужен
                print(f"раздел не определён ({exc}): {display_name}", file=sys.stderr)
                section = None
        try:
            for i in range(doc.page_count):
                text = doc[i].get_text("text").strip()
                if text:
                    out.append({"page": i + 1, "text": text,
                                "document": display_name, "section": section})
        finally:
            doc.close()
    return out


def attach_norms(requirements: list[Requirement], text_facts: list[dict],
                 config: LlmConfig | None = None,
                 llm_norms: list[dict] | None = None) -> tuple[list, str]:
    """Привязать требования к нормативам ПЕРЕЧНЯ ЭТОГО ТОМА (Г.101).

    Один код на CLI и сервер, как и всё остальное в этом модуле (Г.94).
    Порядок ступеней тот же, что везде в проекте: сначала детерминированная
    и бесплатная, потом платная, и вторая работает только над тем, что не
    решила первая.

    1. `link_cited` — обозначение стоит прямо в тексте требования. Проверка
       подстрокой, вызова модели не требует, ошибиться не может.
    2. `link_by_meaning` — параметр норматива не называет. Модель выбирает из
       ЗАКРЫТОГО перечня этого тома либо честно отвечает «не могу». Без
       ключа шаг не выполняется, и требование остаётся без привязки — это
       «не сопоставляли», а не «норматива нет».

    Откуда берётся сам перечень: `llm_norms` — то, что модель прочитала
    попутно с выжимкой (основной путь, Г.103), иначе поиск по тексту
    (запасной путь без ключа). Заголовок раздела с перечнем у каждого
    проектировщика свой, и закрытый список формулировок в регулярке — это
    заточка под уже виденные документы.

    Возвращает `(нормативы, текст раздела отчёта)`. Пустой перечень — не
    ошибка и не повод угадывать нормативы по разделу: раздел отчёта прямо
    говорит, что перечня в томе не найдено (Г.10).
    """
    norms = norms_from_llm(llm_norms or [])
    if not norms:
        norms = find_norms(text_facts)
    if not norms:
        return [], render_norms_section([], requirements)
    pending = link_cited(requirements, norms)
    if config is not None and pending:
        link_by_meaning(pending, norms, config)
    return norms, render_norms_section(norms, requirements)


def attach_rooms_by_name(requirements: list[Requirement],
                         sources: list[tuple]) -> int:
    """Подсказать помещения для требований, где номер не назван (Г.106).

    Найдено сравнением слепого прогона с реальным перечнем нарушений: два
    нарушения из трёх относились к требованиям, которые ПД адресует ТИПОМ
    помещения («в таких-то по назначению помещениях»), а номера этих
    помещений стоят не в предложении, а в экспликации того же тома. У таких
    требований `rooms` пуст, листов для просмотра подобрать не по чему, и в
    отчёте они тонули среди сотен «требование к объекту целиком».

    Механизм существовал (`match_requirement_rooms_by_name`, Г.57), но его
    не вызывал никто — ровно тот класс, что нашёл аудит Г.62. Здесь он
    подключён к конвейеру.

    Результат кладётся в ОТДЕЛЬНОЕ поле: номер в скобках — факт документа,
    совпадение по названию — догадка, и смешивать их нельзя. К разделу
    механизм не привязан: сравниваются слова требования со словами в
    названиях помещений того же документа, каких бы то ни было списков
    терминов здесь нет.

    Возвращает число требований, получивших подсказку.
    """
    room_facts: dict[str, list[dict]] = {}
    for source in sources:
        path, name = source[0], source[1]
        try:
            room_facts[name] = extract_document_facts(path, name).room_facts
        except Exception as exc:  # noqa: BLE001 — один файл не роняет разбор
            print(f"реестр помещений не построен ({exc}): {name}", file=sys.stderr)

    # Слова, которыми назван предмет самого тома, стоят почти в каждом его
    # требовании и потому ничего не различают (Г.106). Считаются по этому
    # же прогону и отдельно для каждого документа: у разных разделов слова
    # разные, а знать их заранее механизм не должен.
    topics: dict[str, set[str]] = {}
    for name in room_facts:
        texts = [r.summary or r.sentence for r in requirements if r.document == name]
        topics[name] = topic_prefixes(texts)

    hinted = 0
    for req in requirements:
        if req.rooms:
            continue  # номер назван явно — догадка не нужна и вредна
        facts = room_facts.get(req.document) or []
        if not facts:
            continue
        rooms = match_requirement_rooms_by_name(
            req.summary or req.sentence, facts, ignore_prefixes=topics.get(req.document))
        if rooms:
            req.rooms_by_name = rooms
            hinted += 1
    return hinted


def record_profile(requirements: list[Requirement], volumes=(), norms=()) -> None:
    """Пополнить профиль разделов тем, что дал этот прогон (Г.105).

    Программа идёт по томам одинаково и ничего не знает ни об одном разделе
    заранее. Знание накапливается здесь: разобрали том — профиль его раздела
    пополнился, следующий том того же раздела разбирается с подсказкой,
    собранной на предыдущих.

    Наблюдения трёх видов и все три — побочный продукт уже сделанной работы,
    отдельных вызовов модели не требующий: устойчивые обороты требований,
    нормативы из перечня тома, реально встреченные типы таблиц.

    Единица счёта — ТОМ: наблюдения группируются по документу, и повторный
    разбор того же файла счётчик не увеличивает (см. `section_profile`).
    Сбой пополнения не роняет прогон — профиль это ускорение, а не результат.
    """
    try:
        by_document: dict[tuple[str, str | None], list[str]] = {}
        for req in requirements:
            by_document.setdefault((req.document, req.section), []).append(
                req.summary or req.sentence)
        for (document, section), texts in by_document.items():
            terms = collect_terms(texts)
            if terms:
                observe(section, KIND_TERM, terms, document=document)

        for volume in volumes:
            if volume.tables_by_kind:
                observe(volume.section, KIND_TABLE,
                        [(kind, kind) for kind, _ in volume.tables_by_kind],
                        document=volume.name)

        by_norm_document: dict[tuple[str, str | None], list[tuple[str, str]]] = {}
        for norm in norms:
            by_norm_document.setdefault((norm.document, norm.section), []).append(
                (norm.designation, norm.designation))
        for (document, section), values in by_norm_document.items():
            observe(section, KIND_NORM, values, document=document)
    except Exception as exc:  # noqa: BLE001 — профиль это ускорение, не результат
        print(f"профиль разделов не пополнен: {type(exc).__name__}: {exc}", file=sys.stderr)


def render_section_knowledge(sections) -> str:
    """Что программа успела узнать о разделах этого прогона."""
    seen: list[str] = []
    for section in sections:
        key = section or ""
        if key in seen:
            continue
        seen.append(key)
    return "\n".join(render_profile(s) for s in seen)


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
            # Г.101 — норматив ИЗ ПЕРЕЧНЯ ЭТОГО ТОМА. Способ привязки назван
            # рядом: «названа в требовании» проверено подстрокой и ошибиться
            # не может, «сопоставлено по смыслу» — гипотеза модели, и
            # показывать их одинаково значило бы выдать второе за первое.
            if first.norm:
                out.append(f"    ← {first.norm} ({first.norm_source})")
    return "\n".join(out)
