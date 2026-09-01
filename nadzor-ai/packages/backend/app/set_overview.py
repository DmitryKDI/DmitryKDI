"""Обзор комплекта — краткая сводка по каждому тому и сравнение разделов
ПД↔РД по составу (не по содержимому листов, а по тому, какие разделы вообще
присутствуют с каждой стороны).

Пользовательский запрос, определивший этот модуль: раньше единственным
способом понять «что вообще в этой пачке файлов» было запустить один из
узких режимов (--kind rooms/equipment/requirements) и читать его вывод —
и весь предыдущий разбор в этой сессии шёл на комплекте одного раздела
(ОВ), из-за чего разбор ощущался «зашитым под ОВ», хотя сама механика
сравнения дисциплину не знает (Г.42). Реальный комплект почти всегда
многораздельный (АР, КР, ОВ, ВК, ЭОМ и другие марки по ГОСТ Р 21.101-2020)
— нужен обзор на уровне «какие разделы есть с каждой стороны», ДО того как
погружаться в сравнение содержимого одного раздела.

Раздел определяется `classification.classify_document` (уже общий —
DISCIPLINE_CODES покрывает АР/КР/КЖ/КМ/ОВ/ВК/ЭОМ/СС/ГП и другие, не
завязан на один раздел) — здесь ничего дисциплино-специфичного не
добавляется, только агрегация уже общего результата по всем файлам сразу.

Комплектность в смысле Г.17 (сверка со списком «Состав документации» —
обозначение→наименование→разработчик) тут не подменяется: тот путь
проверяет, что задуманное по официальному перечню фактически передано;
этот модуль — более грубый и не требующий такого документа: просто что
реально пришло с каждой стороны, по факту файлов на руках."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .classification import classify_document
from .documents import extract_document_facts

# Справочная привязка кода раздела (шифра на штампе/титуле, classification.py)
# к официальному разделу по ПП РФ № 87 «О составе разделов проектной
# документации...» — ТОЛЬКО для подписи в отчёте, ни на что в самом
# сравнении не влияет. Разбивка ИОС на подразделы 1-7 по видам сетей —
# типовая практика, а не жёсткая норма единого номера на все проекты:
# конкретный проект может нумеровать подразделы иначе. Ошибка в подписи
# здесь — не потерянная находка, только неточная человекочитаемая метка.
OFFICIAL_SECTION_LABELS: dict[str, str] = {
    "АР": "Раздел 3 — Архитектурные решения",
    "АС": "Раздел 3 — Архитектурные решения (АС)",
    "КР": "Раздел 4 — Конструктивные и объёмно-планировочные решения",
    "КЖ": "Раздел 4 — Конструктивные решения (КЖ)",
    "КМ": "Раздел 4 — Конструктивные решения (КМ)",
    "ОВ": "Раздел 5 — ИОС2: отопление, вентиляция, кондиционирование",
    "ВК": "Раздел 5 — ИОС1: водоснабжение и водоотведение",
    "НВК": "Раздел 5 — ИОС1: наружные сети водоснабжения/водоотведения",
    "ЭОМ": "Раздел 5 — ИОС3: электроснабжение и электрооборудование",
    "ЭС": "Раздел 5 — ИОС3: электроснабжение",
    "СС": "Раздел 5 — ИОС4: связь, сигнализация, слаботочные системы",
    "АПС": "Раздел 5 — ИОС4: автоматическая пожарная сигнализация",
    "ОПС": "Раздел 5 — ИОС4: охранная и пожарная сигнализация",
    "СКС": "Раздел 5 — ИОС4: структурированные кабельные системы",
    "ТХ": "Раздел 5 — ИОС6: технологические решения",
    "ГП": "Раздел 2 — Схема планировочной организации земельного участка (СПОЗУ/ПЗУ)",
    "ПОС": "Раздел 6 — Проект организации строительства",
    "ПБ": "Раздел 9 — Мероприятия по обеспечению пожарной безопасности",
}


def official_section_label(discipline_code: Optional[str]) -> str:
    """Человекочитаемая подпись раздела для отчёта — код шифра, если
    официальное соответствие не найдено (не гадаем, откуда взялся код
    без известной привязки), название раздела по ПП №87 — если найдено."""
    if not discipline_code:
        return "раздел не определён"
    return OFFICIAL_SECTION_LABELS.get(discipline_code, discipline_code)


@dataclass
class VolumeSummary:
    name: str
    path: str
    discipline_code: Optional[str]
    discipline_source: str  # 'filename' | 'title_page' | 'stamp_text' | 'stamp_vision' | 'none'
    pages: int
    room_count: int
    equipment_count: int
    excluded_count: int


def summarize_volume(pdf_path: str, name: str) -> VolumeSummary:
    """Один файл — одна короткая сводка: раздел, объём, что реально
    извлеклось. Не судит о содержании (для этого есть Приложение Г целиком)
    — только «что это за том и сколько в нём материала», за секунды, без
    LLM."""
    classification = classify_document(pdf_path, name)
    facts = extract_document_facts(pdf_path, name)
    return VolumeSummary(
        name=name, path=pdf_path,
        discipline_code=classification.discipline_code,
        discipline_source=classification.source,
        pages=facts.pages,
        room_count=len({f["key"] for f in facts.room_facts}),
        equipment_count=len({f["key"] for f in facts.equipment_facts}),
        excluded_count=len(facts.excluded),
    )


def summarize_set(paths: list[str]) -> list[VolumeSummary]:
    """Сводка по всем файлам одной стороны. Файл, который не удалось
    прочитать, не роняет весь обзор — пропускается с пометкой в самой
    сводке (видимое состояние, Г.10), а не молча."""
    out: list[VolumeSummary] = []
    for path in paths:
        try:
            out.append(summarize_volume(path, path))
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять обзор остальных
            out.append(VolumeSummary(
                name=path, path=path, discipline_code=None,
                discipline_source=f"ошибка чтения: {exc}",
                pages=0, room_count=0, equipment_count=0, excluded_count=0,
            ))
    return out


def render_volume_summary(summaries: list[VolumeSummary], title: str) -> str:
    lines = [f"=== {title}: {len(summaries)} том(ов) ==="]
    for s in summaries:
        label = official_section_label(s.discipline_code)
        code = f" [{s.discipline_code}]" if s.discipline_code else ""
        lines.append(
            f"  {s.name} — {label}{code} ({s.discipline_source}), {s.pages} стр., "
            f"помещений: {s.room_count}, позиций оборудования: {s.equipment_count}, "
            f"исключено как непроектный материал: {s.excluded_count}"
        )
    return "\n".join(lines)


@dataclass
class SectionCoverage:
    both: set[str] = field(default_factory=set)
    only_before: set[str] = field(default_factory=set)
    only_after: set[str] = field(default_factory=set)
    undetermined_before: list[str] = field(default_factory=list)  # имена файлов без определённого раздела
    undetermined_after: list[str] = field(default_factory=list)


def compare_section_coverage(before: list[VolumeSummary], after: list[VolumeSummary]) -> SectionCoverage:
    """Три категории по Г.9, только ключ — код раздела (АР/КР/ОВ/...), не
    номер помещения. Файл без определённого раздела не попадает ни в одну
    из трёх множеств (сравнивать нечего) — перечисляется отдельно, видимо,
    а не теряется молча (Г.10)."""
    before_codes = {s.discipline_code for s in before if s.discipline_code}
    after_codes = {s.discipline_code for s in after if s.discipline_code}
    return SectionCoverage(
        both=before_codes & after_codes,
        only_before=before_codes - after_codes,
        only_after=after_codes - before_codes,
        undetermined_before=[s.name for s in before if not s.discipline_code],
        undetermined_after=[s.name for s in after if not s.discipline_code],
    )


def _labeled(codes: set[str]) -> str:
    return "; ".join(f"{code} ({official_section_label(code)})" for code in sorted(codes))


def render_section_coverage_report(coverage: SectionCoverage) -> str:
    lines = ["\n=== Сравнение разделов ПД↔РД (по составу, без содержимого листов) ==="]
    if coverage.both:
        lines.append(f"  Есть с обеих сторон: {_labeled(coverage.both)}")
    if coverage.only_before:
        lines.append(f"  Только в ПД — раздел РД не передан или не определён: {_labeled(coverage.only_before)}")
    if coverage.only_after:
        lines.append(f"  Только в РД — раздела не было в переданной ПД: {_labeled(coverage.only_after)}")
    if not (coverage.both or coverage.only_before or coverage.only_after):
        lines.append("  Ни один файл ни с одной стороны — раздел не определён ни для одного тома")
    if coverage.undetermined_before:
        lines.append(f"  ПД, раздел не определён (не входят в сравнение выше): {', '.join(coverage.undetermined_before)}")
    if coverage.undetermined_after:
        lines.append(f"  РД, раздел не определён (не входят в сравнение выше): {', '.join(coverage.undetermined_after)}")
    return "\n".join(lines)
