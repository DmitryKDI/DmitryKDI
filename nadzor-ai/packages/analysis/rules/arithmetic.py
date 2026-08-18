"""Правила арифметики объёмов: заявленное против расчётного по геометрии."""
from __future__ import annotations

from documents.schemas import DocKind

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.collect import act_element
from analysis.rules.common import deviation_percent, parse_amount, parse_geometry, rule_enabled


def check_volumes(project: DocumentSet | None, as_built: DocumentSet | None,
                  ctx) -> list[Detection]:
    """Объём работ в акте расходится с расчётным по геометрии участка."""
    if as_built is None or not rule_enabled(ctx.rules_config, "arithmetic_volume_mismatch"):
        return []
    tolerance = ctx.rules_config.get("rules", {}).get(
        "arithmetic_volume_mismatch", {}).get("tolerance_percent", 10)
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        geometry_text = doc.value("geometry")
        if not geometry_text:
            continue
        geometry = parse_geometry(geometry_text)
        declared, unit = parse_amount(doc.value("volume"))
        expected, expected_unit = _expected(geometry, unit)
        if expected <= 0 or declared <= 0:
            continue
        deviation = deviation_percent(declared, expected)
        if deviation <= tolerance:
            continue
        act_no = doc.title.split("№")[-1].strip()
        found.append(Detection(
            code="D-11",
            title=f"Объём работ в акте № {act_no} ({declared:,.1f} {unit}) расходится с "
                  f"расчётным по геометрии ({expected:,.1f} {expected_unit}) на {deviation}%"
                  .replace(",", " "),
            evidence=[
                Evidence.from_fact(doc.kv()["volume"], doc, "факт", doc.value("volume")),
                Evidence.from_fact(doc.kv()["geometry"], doc, "основание", geometry_text),
            ],
            element=act_element(doc), confidence=0.93,
            norm_refs=["СП 70.13330.2012:5.18.2"], legal_refs=["9.5/5"],
            field_check=f"Инструментальный обмер участка: {geometry_text}. "
                        "Сверить с исполнительной геодезической съёмкой",
            documents_to_request=["Исполнительная схема с геометрическими размерами",
                                  "Акт о приёмке выполненных работ по форме КС-2",
                                  "Товарно-транспортные накладные"],
            detector="arithmetic.volume_mismatch"))
    return found


def _expected(geometry: dict, unit: str) -> tuple[float, str]:
    """Расчётное значение в тех же единицах, что и заявленное."""
    area = geometry.get("area_m2", 0.0)
    thickness = geometry.get("thickness_mm", 0.0)
    if unit in ("м³", "м3"):
        return round(area * thickness / 1000, 2), "м³"
    if unit in ("м²", "м2"):
        return area, "м²"
    return 0.0, unit
