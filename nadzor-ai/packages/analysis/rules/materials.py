"""Правила по материалам и геометрии конструкций.

Переходы T1, T3, T4 — сравнение двух ведомостей конструкций.
Переход T5 — сравнение проектных характеристик с исполнительной документацией.
"""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.collect import act_element, elements_index
from analysis.rules.common import (
    concrete_class,
    frost_grade,
    grade_value,
    latinize,
    rebar_class,
    rule_enabled,
)
from documents.schemas import DocKind

# Поле ведомости → (код расхождения, название параметра, пункт норматива)
FIELD_RULES = [
    ("concrete_class", "D-02", "класс бетона", "СП 63.13330.2018:6.1.6"),
    ("rebar", "D-03", "класс арматуры", "СП 63.13330.2018:6.2.3"),
    ("frost_water", "D-04", "марка по морозостойкости", "ГОСТ 26633-2015:4.3"),
    ("size", "D-01", "сечение или толщина", "СП 20.13330.2016:8.2.2"),
    ("level", "D-10", "отметка", "СП 22.13330.2016:5.6.4"),
]


def compare_specifications(before: DocumentSet | None, after: DocumentSet | None,
                           ctx) -> list[Detection]:
    """Сравнить ведомости конструкций двух состояний."""
    if before is None or after is None:
        return []
    old, new = elements_index(before), elements_index(after)
    found: list[Detection] = []
    for mark, (fact_new, doc_new) in new.items():
        if mark not in old:
            found.append(_added_element(mark, fact_new, doc_new))
            continue
        fact_old, doc_old = old[mark]
        for field, code, label, norm in FIELD_RULES:
            v_old = (fact_old.value or {}).get(field, "").strip()
            v_new = (fact_new.value or {}).get(field, "").strip()
            if not v_old or not v_new or latinize(v_old) == latinize(v_new):
                continue
            found.append(_changed(mark, label, code, norm, v_old, v_new,
                                  fact_old, doc_old, fact_new, doc_new))
    return found


def _added_element(mark: str, fact, doc) -> Detection:
    value = fact.value or {}
    return Detection(
        code="D-09",
        title=f"В новой редакции добавлена конструкция {mark} — {value.get('name', '')}",
        evidence=[Evidence.from_fact(fact, doc, "факт", f"{mark}: {value.get('name', '')}")],
        element={"name": value.get("name", ""), "mark": mark, "level": value.get("level", ""),
                 "axes": value.get("axes", ""), "section": ""},
        confidence=0.97, norm_refs=["ГрК РФ:49 ч.3.8"],
        field_check="Проверить наличие решения о внесении изменений и необходимость "
                    "повторной экспертизы",
        documents_to_request=["Решение застройщика о внесении изменений в проектную документацию"],
        detector="materials.added_element")


def _changed(mark: str, label: str, code: str, norm: str, v_old: str, v_new: str,
             fact_old, doc_old, fact_new, doc_new) -> Detection:
    v_old_short, v_new_short = _display(label, v_old), _display(label, v_new)
    downgrade = _is_downgrade(label, v_old_short, v_new_short)
    direction = "снижение" if downgrade else "изменение"
    return Detection(
        code=code,
        title=f"{label.capitalize()} конструкции {mark}: {v_old_short} → {v_new_short} "
              f"({direction} относительно предыдущей редакции)",
        evidence=[
            Evidence.from_fact(fact_old, doc_old, "требование", f"{mark}: {v_old}"),
            Evidence.from_fact(fact_new, doc_new, "факт", f"{mark}: {v_new}"),
        ],
        element=_element_of(fact_new),
        severity=None if downgrade else "major",
        confidence=0.96, norm_refs=[norm],
        field_check=_field_check(label, mark),
        documents_to_request=["Расчёт, обосновывающий изменение",
                              "Решение о внесении изменений в проектную документацию"],
        detector="materials.changed_parameter")


def _display(label: str, value: str) -> str:
    """Показывать только сравниваемую часть значения."""
    if "морозостойк" in label:
        return frost_grade(value) or value
    return value


def _is_downgrade(label: str, v_old: str, v_new: str) -> bool:
    if "бетон" in label or "морозостойк" in label:
        return grade_value(latinize(v_new)) < grade_value(latinize(v_old))
    if "арматур" in label:
        return grade_value(latinize(v_new)) < grade_value(latinize(v_old))
    if "толщина" in label or "сечение" in label:
        return grade_value(v_new) < grade_value(v_old)
    return False


def _element_of(fact) -> dict:
    value = fact.value or {}
    return {"name": value.get("name", ""), "mark": value.get("mark", ""),
            "level": value.get("level", ""), "axes": value.get("axes", ""), "section": ""}


def _field_check(label: str, mark: str) -> str:
    if "бетон" in label:
        return (f"Неразрушающий контроль прочности бетона конструкции {mark} "
                "либо отбор кернов на участке")
    if "арматур" in label:
        return f"Контроль армирования конструкции {mark} магнитным методом или вскрытием"
    if "толщина" in label or "сечение" in label:
        return f"Инструментальный обмер сечения конструкции {mark}"
    if "отметка" in label:
        return f"Геодезическая исполнительная съёмка отметки конструкции {mark}"
    return f"Осмотр конструкции {mark} с фотофиксацией"


def check_as_built_materials(project: DocumentSet | None, as_built: DocumentSet | None,
                             ctx) -> list[Detection]:
    """Класс материала в акте освидетельствования ниже проектного."""
    if project is None or as_built is None:
        return []
    if not rule_enabled(ctx.rules_config, "material_class_downgrade"):
        return []
    design = elements_index(project)
    found: list[Detection] = []
    for doc in as_built.of_kind(DocKind.AOSR):
        mark = doc.value("mark").strip()
        if mark not in design:
            continue
        fact_design, doc_design = design[mark]
        material_fact = doc.kv().get("material")
        if material_fact is None:
            continue
        found.extend(_compare_material(mark, str(material_fact.value), material_fact, doc,
                                       fact_design, doc_design))
    return found


def _compare_material(mark: str, material: str, material_fact, doc, fact_design,
                      doc_design) -> list[Detection]:
    design_value = fact_design.value or {}
    checks = [
        (concrete_class(material), concrete_class(design_value.get("concrete_class", "")),
         "D-02", "класс бетона", "СП 63.13330.2018:6.1.6"),
        (rebar_class(material), rebar_class(design_value.get("rebar", "")),
         "D-03", "класс арматуры", "СП 63.13330.2018:6.2.3"),
        (frost_grade(material), frost_grade(design_value.get("frost_water", "")),
         "D-04", "марка по морозостойкости", "ГОСТ 26633-2015:4.3"),
    ]
    out = []
    for actual, required, code, label, norm in checks:
        if not actual or not required or grade_value(actual) >= grade_value(required):
            continue
        act_no = doc.title.split("№")[-1].strip()
        out.append(Detection(
            code=code,
            title=f"{label.capitalize()} в акте освидетельствования № {act_no} ниже проектного: "
                  f"{actual} против {required}",
            evidence=[
                Evidence.from_fact(fact_design, doc_design, "требование", f"{mark}: {required}"),
                Evidence.from_fact(material_fact, doc, "факт", material),
            ],
            element=act_element(doc), confidence=0.98, norm_refs=[norm],
            legal_refs=["9.4/2"],
            field_check=f"Отбор кернов либо неразрушающий контроль прочности конструкции {mark}, "
                        f"отм. {doc.value('level')}, оси {doc.value('axes')}",
            documents_to_request=["Протокол испытаний контрольных образцов",
                                  "Общий журнал работ за период выполнения работ",
                                  "Документы о качестве применённых материалов"],
            detector="materials.as_built_downgrade"))
    return out
