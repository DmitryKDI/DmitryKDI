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

from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from .classification import PAGE_KIND_DRAWING, classify_document, classify_page_kind
from .set_overview import official_section_label
from .table_registry import classify_table_page

# Порог «текстовый слой практически пуст»: штамп листа даёт порядка
# 300-500 символов даже там, где вся остальная графика в кривых (замер
# Г.59). Всё, что ниже, содержательного текста не несёт.
POOR_TEXT_LAYER_CHARS = 200


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
    return "\n".join(lines)


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
