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
from .llm import LlmConfig
from .norms_registry import (
    find_norms,
    link_by_meaning,
    link_cited,
    norms_from_llm,
    render_norms_section,
)
from .requirement_registry import Requirement, _normalize_for_dedup
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
