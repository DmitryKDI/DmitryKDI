"""Правила хронологии: даты документов и последовательность событий."""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.collect import act_element, passport_index
from analysis.rules.common import parse_ru_date, rule_enabled
from documents.schemas import DocKind


def check_material_chronology(project: DocumentSet | None, as_built: DocumentSet | None,
                              ctx) -> list[Detection]:
    """Акт освидетельствования подписан раньше даты документа о качестве материала."""
    if as_built is None or not rule_enabled(ctx.rules_config, "chronology_aosr_vs_passport"):
        return []
    passports = passport_index(as_built)
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        act_date = parse_ru_date(doc.value("act_date"))
        passport_no = doc.value("material_doc").strip()
        passport = passports.get(passport_no)
        if act_date is None or passport is None:
            continue
        issue_date = parse_ru_date(passport.value("issue_date"))
        if issue_date is None or issue_date <= act_date:
            continue
        act_no = doc.title.split("№")[-1].strip()
        found.append(Detection(
            code="D-05",
            title=f"Акт освидетельствования № {act_no} от {act_date:%d.%m.%Y} подписан раньше "
                  f"даты паспорта качества {passport_no} от {issue_date:%d.%m.%Y}",
            evidence=[
                Evidence.from_fact(doc.kv()["act_date"], doc, "факт", f"{act_date:%d.%m.%Y}"),
                Evidence.from_fact(passport.kv()["issue_date"], passport, "факт",
                                   f"{issue_date:%d.%m.%Y}"),
            ],
            element=act_element(doc), confidence=0.99,
            norm_refs=["РД-11-05-2007:4", "РД-11-02-2006:5"], legal_refs=["9.5/5"],
            field_check="Сверить фактическую дату выполнения работ с записями общего журнала "
                        "работ и товарно-транспортными накладными на поставку материала",
            documents_to_request=["Товарно-транспортные накладные на поставку бетонной смеси",
                                  "Общий журнал работ за указанный период"],
            detector="chronology.aosr_vs_passport"))
    return found


def check_permit_chronology(project: DocumentSet | None, as_built: DocumentSet | None,
                            ctx) -> list[Detection]:
    """Работы выполнены до даты выдачи разрешения на строительство."""
    if as_built is None or not rule_enabled(ctx.rules_config, "chronology_work_before_permit"):
        return []
    permit_date = parse_ru_date(ctx.object_card.get("permit_date_ru", ""))
    if permit_date is None:
        return []
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        act_date = parse_ru_date(doc.value("act_date"))
        if act_date is None or act_date >= permit_date:
            continue
        act_no = doc.title.split("№")[-1].strip()
        found.append(Detection(
            code="D-06",
            title=f"Работы по акту № {act_no} от {act_date:%d.%m.%Y} выполнены до выдачи "
                  f"разрешения на строительство от {permit_date:%d.%m.%Y}",
            evidence=[Evidence.from_fact(doc.kv()["act_date"], doc, "факт",
                                         f"{act_date:%d.%m.%Y}")],
            element=act_element(doc), confidence=0.99,
            norm_refs=["СП 48.13330.2019:9.2"], legal_refs=["9.5/5"],
            field_check="Установить фактические сроки выполнения работ по журналу работ",
            documents_to_request=["Общий журнал работ", "Договор подряда"],
            detector="chronology.work_before_permit"))
    return found
