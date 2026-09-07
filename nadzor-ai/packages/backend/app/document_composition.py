"""Состав тома: сколько листов чертежей, сколько таблиц и каких (Г.95).

Работает на ЛЮБОМ виде документации — проектной, рабочей, исполнительной:
механизм один, отдельного «разбора РД» в коде нет и быть не должно
(прямое требование пользователя: «РД имеет тот же принцип, что и ПД, код
общий на любой вид документации»). Поводом послужила стадия разбора
рабочей документации: «если в РД нет текста для сводки, то просто он
должен говорить, что в документе столько листов графической части, столько
спецификаций и т.д.», — но ограничивать модуль этой стороной значило бы
завести раздельные пути там, где различия нет.

Почему это НЕ запасной вариант на случай неудачи, а самостоятельный
результат. У рабочей документации текстового слоя почти нет по природе:
CAD-экспорт переводит подписи в кривые, и `get_text` возвращает либо
пусто, либо один штамп (Г.8, подтверждено измерением в Г.44/Г.59 — на
листах таблиц ~460 символов, только штамп). Если на такой том выдать
пустую сводку требований, инспектор увидит то же, что при сбое связи, —
ровно та подмена, которую запрещает Г.10. Поэтому состав считается ВСЕГДА,
и по нему видно, чего от тома ждать: 700 листов чертежей и 3 спецификации
— это работа для сверки по чертежам, а не для чтения текста.

Считается детерминированно, без ЛЛМ и за секунды: тип листа —
`classification.classify_page_kind` (формат листа и число векторных
путей), тип таблицы — общий реестр `table_registry`, тот же, что
использует сверка назначений (Г.88). Ни одной раздело-специфичной строки
здесь нет: новый тип таблицы подключается записью в реестре.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from .classification import PAGE_KIND_DRAWING, classify_document, classify_page_kind
from .set_overview import official_section_label
from .stamp import read_stamp
from .table_registry import classify_table_page

# Г.98 — наименование чертежа принимается, только если начинается как
# НАЗВАНИЕ ЛИСТА по ГОСТ Р 21.1101. Замер на реальных томах показал, почему
# фильтр обязателен: зона штампа на листе формата А0/А1 — это четверть
# площади, и в неё попадает содержимое самого чертежа. `read_stamp` честно
# возвращал оттуда первые длинные строки, и в перечень шли обрывки вроде
# «икация помещений 1 этажа Наименование» (кусок таблицы экспликации) и
# «188 Холодный 186 Овощной» (подписи помещений на плане) — причём второе
# на листе, где шифр и номер листа прочитаны верно, то есть признак
# «штамп настоящий» здесь не спасает. Показать инспектору выдуманное
# название хуже, чем честно сказать «не прочитано» (Г.11/Г.10).
#
# Список открывающих слов общий по ГОСТ и к разделу не привязан: план,
# схема, разрез, узел, фасад, ведомость, спецификация, таблица и т.п.
_SHEET_TITLE_RE = re.compile(
    r"^(план|схема|принципиальная|разрез|узел|фасад|деталь|ведомость|"
    r"спецификация|таблица|характеристика|экспликация|аксонометри\w*|"
    r"общие\s+данные|условные\s+обозначения|генеральный\s+план)\b",
    re.IGNORECASE)


def _clean_sheet_name(name: str | None) -> str | None:
    """Наименование листа или None, если прочитанное на название не похоже."""
    if not name:
        return None
    cleaned = " ".join(name.split())
    return cleaned if _SHEET_TITLE_RE.match(cleaned) else None


# Порог «текстовый слой практически пуст»: штамп листа даёт порядка
# 300-500 символов даже там, где вся остальная графика в кривых (замер
# Г.59). Всё, что ниже, содержательного текста не несёт.
POOR_TEXT_LAYER_CHARS = 200


@dataclass
class SheetEntry:
    """Один лист графической части: номер и наименование чертежа.

    Г.98 — наименование берётся из ШТАМПА текстом. Он остаётся текстом
    заметно чаще, чем содержимое листа (Г.59: на листах таблиц в кривых
    текстовый слой несёт только штамп), поэтому перечень графики строится
    без единого вызова модели. `name=None` — штамп тоже в кривых: лист всё
    равно попадает в перечень как «наименование не прочитано», а не
    исчезает из сводки (Г.10).
    """
    page: int
    name: str | None = None


@dataclass
class VolumeComposition:
    """Из чего физически состоит том — без суждений о содержании."""
    name: str
    section: str | None = None
    pages: int = 0
    drawing_pages: int = 0
    text_pages: int = 0
    #  Лист, где текстового слоя нет вовсе или он беднее штампа (Г.8).
    pages_without_text: int = 0
    # [(kind, count)] — типы таблиц из общего реестра, по убыванию частоты.
    tables_by_kind: list[tuple[str, int]] = field(default_factory=list)
    # Листы с содержательной прозой: из них и только из них может выйти
    # сводка требований. Их число прямо объясняет размер сводки.
    prose_pages: int = 0
    # Г.98 — перечень листов графической части с наименованиями: сводка
    # обязана покрывать ВЕСЬ документ, а не только его текстовую часть.
    sheets: list[SheetEntry] = field(default_factory=list)
    error: str | None = None


def describe_volume(pdf_path: str, name: str,
                    manual_section: str | None = None) -> VolumeComposition:
    """Состав одного тома. Битый файл не роняет разбор комплекта: причина
    попадает в поле `error` и в отчёт (Г.10).

    `manual_section` — раздел, заданный инспектором вручную (Г.97): он
    побеждает автоматическое определение и отменяет его.
    """
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:  # noqa: BLE001 — один битый файл не роняет прогон
        return VolumeComposition(name=name, error=f"файл не прочитан: {exc}")

    try:
        if manual_section:
            section = manual_section
        else:
            try:
                section = classify_document(pdf_path, name).discipline_code
            except Exception:  # noqa: BLE001 — раздел не определён, состав всё равно нужен
                section = None

        out = VolumeComposition(name=name, section=section, pages=doc.page_count)
        text_facts: list[dict] = []
        for i in range(doc.page_count):
            page = doc[i]
            text = page.get_text("text").strip()
            text_facts.append({"page": i + 1, "text": text})
            if classify_page_kind(page) == PAGE_KIND_DRAWING:
                out.drawing_pages += 1
                try:
                    name = _clean_sheet_name(read_stamp(page).sheet_name)
                except Exception:  # noqa: BLE001 — нечитаемый штамп не роняет разбор
                    name = None
                out.sheets.append(SheetEntry(page=i + 1, name=name))
            else:
                out.text_pages += 1
            if len(text) < POOR_TEXT_LAYER_CHARS:
                out.pages_without_text += 1

        kinds: Counter[str] = Counter()
        for i in range(doc.page_count):
            kind = classify_table_page(text_facts, i + 1)
            if kind is not None:
                kinds[kind.kind] += 1
        out.tables_by_kind = kinds.most_common()

        # Проза — то, из чего вообще может получиться сводка требований:
        # текстовый лист с содержательным объёмом текста. Таблицы сюда не
        # попадают: их читает другой механизм (Г.88), не извлечение из прозы.
        table_pages = {i + 1 for i in range(doc.page_count)
                       if classify_table_page(text_facts, i + 1) is not None}
        out.prose_pages = sum(
            1 for f in text_facts
            if len(f["text"]) >= POOR_TEXT_LAYER_CHARS and f["page"] not in table_pages
        )
        return out
    finally:
        doc.close()


def render_composition(volumes: list[VolumeComposition]) -> str:
    """Отчёт о составе — то, что инспектор читает вместо пустой сводки.

    Обязан объяснять, чего ждать от требований: «текста нет» без пояснения
    неотличимо для читателя от «программа не сработала».
    """
    lines = ["=== Состав комплекта ==="]
    for v in volumes:
        if v.error:
            lines.append(f"\n{v.name}: {v.error}")
            continue
        label = official_section_label(v.section) if v.section else "раздел не определён"
        lines.append(f"\n{v.name} — {label}")
        lines.append(f"  листов всего: {v.pages}; из них чертежей: {v.drawing_pages}, "
                     f"текстовых: {v.text_pages}")
        if v.pages_without_text:
            lines.append(f"  без текстового слоя (подписи в кривых): {v.pages_without_text}")
        if v.tables_by_kind:
            parts = ", ".join(f"{_TABLE_LABELS.get(k, k)}: {n}" for k, n in v.tables_by_kind)
            lines.append(f"  таблиц по типам — {parts}")
        if v.prose_pages:
            lines.append(f"  листов со связным текстом: {v.prose_pages} — "
                         f"по ним строится сводка требований")
        else:
            lines.append("  связного текста нет — сводка требований из этого тома НЕ строится. "
                         "Это не сбой: подписи чертежей переведены в кривые, "
                         "проверять их нужно по изображению листа.")
        lines.extend(_render_sheets(v.sheets))
    return "\n".join(lines)


def _pages_range(pages: list[int]) -> str:
    """«1-3, 7» вместо «1, 2, 3, 7»: перечень на сотни листов иначе нечитаем."""
    out: list[str] = []
    start = prev = pages[0]
    for page in pages[1:] + [None]:
        if page is not None and page == prev + 1:
            prev = page
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if page is not None:
            start = prev = page
    return ", ".join(out)


def _render_sheets(sheets: list[SheetEntry]) -> list[str]:
    """Перечень графической части: наименование → листы.

    Г.98/Г.90 — одинаковые наименования сворачиваются в одну строку со
    списком листов: у тома в 700 листов перечень по строке на лист
    нечитаем, а сжимается ВИД, не данные — ни один лист не теряется.
    """
    if not sheets:
        return []
    by_name: dict[str, list[int]] = {}
    unnamed: list[int] = []
    for sheet in sheets:
        if sheet.name:
            by_name.setdefault(sheet.name, []).append(sheet.page)
        else:
            unnamed.append(sheet.page)

    lines = ["  графическая часть:"]
    for name, pages in sorted(by_name.items(), key=lambda kv: kv[1][0]):
        lines.append(f"    листы {_pages_range(sorted(pages))} — {name}")
    if unnamed:
        lines.append(f"    листы {_pages_range(sorted(unnamed))} — наименование не прочитано "
                     f"(штамп тоже в кривых), нужен просмотр изображения")
    return lines


# Человеческие названия типов таблиц реестра. Сам реестр держит нормативный
# термин и регулярку; здесь только подпись для отчёта, чтобы «work_volumes»
# не попадало инспектору на глаза.
_TABLE_LABELS = {
    "equipment_specification": "спецификации оборудования",
    "work_volumes": "ведомости объёмов работ",
    "ventilation_balance": "таблицы воздухообменов",
    "water_balance": "балансы водопотребления",
    "electrical_loads": "таблицы электрических нагрузок",
    "cable_log": "кабельные журналы",
    "steel_consumption": "ведомости расхода стали",
    "shipping_marks": "ведомости отправочных марок",
    "finishing_schedule": "ведомости отделки",
    "openings_schedule": "ведомости проёмов",
    "site_balance": "балансы территории",
}
