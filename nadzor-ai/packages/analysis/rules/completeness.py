"""Правила комплектности исполнительной документации."""
from __future__ import annotations

import re

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.collect import act_element, report_index
from analysis.rules.common import rule_enabled
from documents.schemas import DocKind

# Обязательные реквизиты акта по форме РД-11-02-2006.
REQUIRED_FIELDS = {
    "work": "наименование освидетельствуемых работ",
    "material": "применённые материалы",
    "material_doc": "документ о качестве материала",
    "volume": "объём выполненных работ",
    "act_date": "дата освидетельствования",
    "contractor": "лицо, осуществляющее строительство",
}

# «Бетон» как самостоятельное слово: «асфальтобетонная смесь» под правило не подпадает.
CONCRETE_RE = re.compile(r"(?<![а-яё])бетон", re.IGNORECASE)


def check_test_reports(project: DocumentSet | None, as_built: DocumentSet | None,
                       ctx) -> list[Detection]:
    """К акту на бетонные работы не приложен протокол испытаний образцов."""
    if as_built is None or not rule_enabled(ctx.rules_config, "completeness_test_reports"):
        return []
    reports = report_index(as_built)
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        material = doc.value("material")
        if not CONCRETE_RE.search(material):
            continue
        act_no = doc.title.split("№")[-1].strip()
        attachments = " ".join(str(f.value) for f in doc.facts_of("attachment")).lower()
        if "протокол" in attachments or act_no in reports:
            continue
        anchor = doc.kv().get("material") or doc.kv().get("act_date")
        if anchor is None:
            continue
        found.append(Detection(
            code="D-07",
            title=f"К акту освидетельствования № {act_no} не приложен протокол испытаний "
                  "контрольных образцов бетона",
            evidence=[Evidence.from_fact(anchor, doc, "факт", doc.value("material"))],
            element=act_element(doc), confidence=0.94,
            norm_refs=["РД-11-02-2006:7", "СП 70.13330.2012:5.3.5"], legal_refs=["9.5/5"],
            field_check="Неразрушающий контроль прочности бетона конструкции на месте "
                        "либо отбор кернов при отсутствии протокола",
            documents_to_request=["Протокол испытаний контрольных образцов бетона",
                                  "Журнал бетонных работ"],
            detector="completeness.test_reports"))
    return found


def check_required_fields(project: DocumentSet | None, as_built: DocumentSet | None,
                          ctx) -> list[Detection]:
    """Не заполнены обязательные реквизиты формы акта."""
    if as_built is None or not rule_enabled(ctx.rules_config, "completeness_required_fields"):
        return []
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        missing = [label for key, label in REQUIRED_FIELDS.items() if not doc.value(key).strip()]
        if not missing:
            continue
        anchor = next(iter(doc.kv().values()), None)
        if anchor is None:
            continue
        act_no = doc.title.split("№")[-1].strip()
        found.append(Detection(
            code="D-08",
            title=f"В акте освидетельствования № {act_no} не заполнены обязательные реквизиты: "
                  + ", ".join(missing),
            evidence=[Evidence.from_fact(anchor, doc, "факт", "; ".join(missing))],
            element=act_element(doc), confidence=0.99,
            norm_refs=["РД-11-02-2006:5"], legal_refs=["9.5/5"],
            field_check="Истребовать надлежаще оформленный акт освидетельствования",
            documents_to_request=["Акт освидетельствования в надлежащей форме"],
            detector="completeness.required_fields"))
    return found
