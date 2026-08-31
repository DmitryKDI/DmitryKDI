"""Кросс-проверка помещений ПД↔РД по всему комплекту (Приложение Г.9/Г.23).

В отличие от `router.py` (сравнивает реестры внутри уже сопоставленной пары
листов) и `anchor_pages.py` (ищет ВСЕ страницы РД, где встречается уже
подтверждённый якорь), здесь номер помещения сравнивается напрямую по всему
комплекту, без привязки к какой-то конкретной паре листов:

  1. Извлечь ПОЛНЫЙ реестр помещений из ПД (все номера со всех страниц).
  2. Извлечь ПОЛНЫЙ реестр помещений из РД.
  3. Для каждого номера из ПД: нет в РД → находка; есть — сравнить
     название/площадь.

Дёшево (без LLM) и не требует, чтобы номер попал в какую-то сопоставленную
пару листов — то же самое узкое место, которое решает `registry_diff.py`
для обеих сторон category-diff'ом; этот модуль — тот же принцип, оформленный
как находки с severity/detail, готовые для отчёта инспектору.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .matching import DocumentInput


@dataclass
class RoomFinding:
    """Находка по одному помещению."""
    room_key: str
    room_name_pd: str  # название в ПД
    finding_type: str  # 'missing_in_rd' | 'name_changed' | 'area_changed'
    detail: str = ""  # человекочитаемое описание
    severity: str = "существенно"  # критично | существенно | незначительно
    room_name_rd: Optional[str] = None  # название в РД (None = не найдено)


@dataclass
class RoomCrossCheckResult:
    """Результат кросс-проверки."""
    findings: list[RoomFinding] = field(default_factory=list)
    missing_anchors: list[str] = field(default_factory=list)  # номера из ПД, не найденные в РД
    visual_check_needed: list[str] = field(default_factory=list)  # номера, требующие ручной проверки
    total_pd_rooms: int = 0
    total_rd_rooms: int = 0
    matched_rooms: int = 0
    unmatched_rooms: int = 0


def _build_room_index(files: list[DocumentInput]) -> dict[str, dict]:
    """Индекс {room_key: {name, area, pages, file_idx}} по всем файлам стороны."""
    index: dict[str, dict] = {}
    for file_idx, entry in enumerate(files):
        for fact in entry.room_facts:
            key = fact.get("key")
            if not key:
                continue
            if key not in index:
                index[key] = {
                    "name": fact.get("name", ""),
                    "area": fact.get("area"),
                    "pages": [],
                    "file_idx": file_idx,
                }
            index[key]["pages"].append(fact["page"])
    return index


def cross_check_rooms(
    before_files: list[DocumentInput],
    after_files: list[DocumentInput],
) -> RoomCrossCheckResult:
    """Кросс-проверка помещений ПД↔РД по всему комплекту."""
    pd_index = _build_room_index(before_files)
    rd_index = _build_room_index(after_files)

    result = RoomCrossCheckResult(
        total_pd_rooms=len(pd_index),
        total_rd_rooms=len(rd_index),
    )

    for room_key, pd_info in sorted(pd_index.items()):
        if room_key not in rd_index:
            result.findings.append(RoomFinding(
                room_key=room_key,
                room_name_pd=pd_info["name"],
                finding_type="missing_in_rd",
                detail=f"Отсутствует в РД: {room_key} «{pd_info['name']}»",
                severity="существенно",
            ))
            result.unmatched_rooms += 1
            result.missing_anchors.append(room_key)
            continue

        rd_info = rd_index[room_key]
        result.matched_rooms += 1

        pd_name = pd_info["name"]
        rd_name = rd_info["name"]
        if pd_name and rd_name and pd_name != rd_name and not _is_minor_variation(pd_name, rd_name):
            result.findings.append(RoomFinding(
                room_key=room_key,
                room_name_pd=pd_name,
                room_name_rd=rd_name,
                finding_type="name_changed",
                detail=f"Название изменено: ПД «{pd_name}» → РД «{rd_name}»",
                severity="незначительно",
            ))
            result.unmatched_rooms += 1

        pd_area = pd_info.get("area")
        rd_area = rd_info.get("area")
        if pd_area and rd_area and pd_area != rd_area:
            result.findings.append(RoomFinding(
                room_key=room_key,
                room_name_pd=pd_name,
                finding_type="area_changed",
                detail=f"Площадь изменена: ПД {pd_area} → РД {rd_area}",
                severity="незначительно",
            ))
            result.unmatched_rooms += 1

    return result


def _is_minor_variation(name_a: str, name_b: str) -> bool:
    """Разница между названиями незначительна — уточнение, а не изменение.

    Например: «Венткамера» → «Венткамера П10» — уточнение. Но «Венткамера»
    → «Тепловая» — реальное изменение."""
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    if a == b:
        return True
    if b.startswith(a) or a.startswith(b):
        return True
    words_a, words_b = a.split(), b.split()
    common = 0
    for wa, wb in zip(words_a, words_b):
        if wa != wb:
            break
        common += 1
    return common >= 2


def render_cross_check_report(result: RoomCrossCheckResult) -> str:
    """Печатный отчёт кросс-проверки."""
    lines = [
        "=== Кросс-проверка помещений ПД↔РД ===",
        f"Всего помещений в ПД: {result.total_pd_rooms}",
        f"Всего помещений в РД: {result.total_rd_rooms}",
        f"Найдено в обеих: {result.matched_rooms}",
        f"Расхождения: {result.unmatched_rooms}",
        f"Находки: {len(result.findings)}",
    ]

    if result.missing_anchors:
        lines.append(f"\nНомера из ПД, не найденные в РД ({len(result.missing_anchors)}):")
        lines.append("  " + ", ".join(result.missing_anchors[:20]))
        if len(result.missing_anchors) > 20:
            lines.append(f"  ... и ещё {len(result.missing_anchors) - 20}")

    by_type: dict[str, list[RoomFinding]] = {}
    for f in result.findings:
        by_type.setdefault(f.finding_type, []).append(f)

    for ftype, findings in sorted(by_type.items()):
        lines.append(f"\n--- {ftype} ({len(findings)}) ---")
        for f in findings[:10]:
            lines.append(f"  [{f.severity}] {f.detail}")
        if len(findings) > 10:
            lines.append(f"  ... и ещё {len(findings) - 10}")

    return "\n".join(lines)
