"""Кросс-проверка оборудования ПД↔РД по всему комплекту (Приложение Г.20).

Тот же принцип, что и `room_cross_check.py`, только по реестру ведомости
оборудования (`equipment.py`), а не помещений:

  1. Извлечь ПОЛНЫЙ реестр позиций оборудования из ПД (все коды со всех
     страниц).
  2. Извлечь ПОЛНЫЙ реестр позиций оборудования из РД.
  3. Для каждой позиции из ПД: нет в РД → находка; есть, но другое
     количество → находка.

Сравнение по названию сюда намеренно НЕ включено: `equipment.py` ещё не
чист от шума извлечения (Г.28 — короткие «позиции» с числовыми
«названиями» — координатные отметки чертежа, не реальные названия
оборудования), и сравнение двух зашумлённых строк дало бы больше ложных
находок, чем реальных. Присутствие/отсутствие кода позиции и разница в
количестве — единственные два сигнала, которые Г.11 (правило только по
наблюдённому) пока позволяет здесь заявлять с уверенностью.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .matching import DocumentInput


@dataclass
class EquipFinding:
    """Находка по одной позиции оборудования."""
    equip_key: str
    equip_name_pd: str  # название в ПД
    finding_type: str  # 'missing_in_rd' | 'missing_in_pd' | 'qty_changed'
    detail: str = ""
    severity: str = "существенно"
    equip_name_rd: Optional[str] = None


@dataclass
class EquipCrossCheckResult:
    """Результат кросс-проверки оборудования."""
    findings: list[EquipFinding] = field(default_factory=list)
    missing_in_rd: list[str] = field(default_factory=list)  # коды из ПД, не найденные в РД
    missing_in_pd: list[str] = field(default_factory=list)  # коды из РД, не найденные в ПД
    total_pd_equip: int = 0
    total_rd_equip: int = 0
    matched_equip: int = 0
    unmatched_equip: int = 0


def _build_equip_index(files: list[DocumentInput]) -> dict[str, dict]:
    """Индекс {equip_key: {name, qty, parent, pages, file_idx}} по всем файлам стороны."""
    index: dict[str, dict] = {}
    for file_idx, entry in enumerate(files):
        for fact in entry.equipment_facts:
            key = fact.get("key")
            if not key:
                continue
            if key not in index:
                index[key] = {
                    "name": fact.get("name", ""),
                    "qty": fact.get("qty"),
                    "parent": fact.get("parent"),
                    "pages": [],
                    "file_idx": file_idx,
                }
            index[key]["pages"].append(fact["page"])
    return index


def cross_check_equipment(
    before_files: list[DocumentInput],
    after_files: list[DocumentInput],
) -> EquipCrossCheckResult:
    """Кросс-проверка позиций ведомости оборудования ПД↔РД по всему комплекту."""
    pd_index = _build_equip_index(before_files)
    rd_index = _build_equip_index(after_files)

    result = EquipCrossCheckResult(
        total_pd_equip=len(pd_index),
        total_rd_equip=len(rd_index),
    )

    for key, pd_info in sorted(pd_index.items()):
        if key not in rd_index:
            result.findings.append(EquipFinding(
                equip_key=key,
                equip_name_pd=pd_info["name"],
                finding_type="missing_in_rd",
                detail=f"Отсутствует в РД: {key} «{pd_info['name']}»",
                severity="существенно",
            ))
            result.unmatched_equip += 1
            result.missing_in_rd.append(key)
            continue

        rd_info = rd_index[key]
        result.matched_equip += 1

        pd_qty, rd_qty = pd_info.get("qty"), rd_info.get("qty")
        if pd_qty and rd_qty and pd_qty != rd_qty:
            result.findings.append(EquipFinding(
                equip_key=key,
                equip_name_pd=pd_info["name"],
                equip_name_rd=rd_info["name"],
                finding_type="qty_changed",
                detail=f"{key} «{pd_info['name']}»: количество ПД {pd_qty} → РД {rd_qty}",
                severity="существенно",
            ))
            result.unmatched_equip += 1

    for key, rd_info in sorted(rd_index.items()):
        if key not in pd_index:
            result.findings.append(EquipFinding(
                equip_key=key,
                equip_name_pd="",
                equip_name_rd=rd_info["name"],
                finding_type="missing_in_pd",
                detail=f"Добавлено в РД (не было в ПД): {key} «{rd_info['name']}»",
                severity="незначительно",
            ))
            result.missing_in_pd.append(key)

    return result


def render_equip_cross_check_report(result: EquipCrossCheckResult) -> str:
    """Печатный отчёт кросс-проверки оборудования."""
    lines = [
        "=== Кросс-проверка оборудования ПД↔РД (Г.20) ===",
        f"Всего позиций в ПД: {result.total_pd_equip}",
        f"Всего позиций в РД: {result.total_rd_equip}",
        f"Найдено в обеих: {result.matched_equip}",
        f"Расхождения: {result.unmatched_equip}",
        f"Находки: {len(result.findings)}",
    ]

    if result.missing_in_rd:
        lines.append(f"\nПозиции из ПД, не найденные в РД ({len(result.missing_in_rd)}):")
        lines.append("  " + ", ".join(result.missing_in_rd[:20]))
        if len(result.missing_in_rd) > 20:
            lines.append(f"  ... и ещё {len(result.missing_in_rd) - 20}")

    by_type: dict[str, list[EquipFinding]] = {}
    for f in result.findings:
        by_type.setdefault(f.finding_type, []).append(f)

    for ftype, findings in sorted(by_type.items()):
        lines.append(f"\n--- {ftype} ({len(findings)}) ---")
        for f in findings[:10]:
            lines.append(f"  [{f.severity}] {f.detail}")
        if len(findings) > 10:
            lines.append(f"  ... и ещё {len(findings) - 10}")

    return "\n".join(lines)
