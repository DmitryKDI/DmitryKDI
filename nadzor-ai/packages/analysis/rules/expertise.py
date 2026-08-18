"""Правила по заключению экспертизы: повторная экспертиза и учёт замечаний."""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.common import parse_ru_date
from documents.schemas import DocKind

UNRESOLVED_MARKERS = ("не представлен", "не устранен", "не устранён", "отсутств", "—", "-")

# Слова замечания, по которым проверяется его отражение в новой редакции.
STOP_WORDS = {"представить", "выполнить", "уточнить", "расчёт", "расчет", "результаты",
              "проектной", "документации", "требования", "провести", "и", "в", "на", "по",
              "с", "для", "зоне", "стадии", "учётом", "учетом", "также"}


def check_repeat_expertise(before: DocumentSet | None, after: DocumentSet | None,
                           ctx) -> list[Detection]:
    """Изменения внесены после положительного заключения — нужна повторная экспертиза."""
    if after is None:
        return []
    expertise_date = parse_ru_date(ctx.object_card.get("expertise_date_ru", ""))
    if expertise_date is None:
        return []
    changes = [(f, d) for d in after.of_kind(DocKind.PD) for f in d.facts_of("change")]
    if not changes:
        return []
    doc_date = next((d.doc_date for d in after.of_kind(DocKind.PD) if d.doc_date), None)
    if doc_date is None or doc_date <= expertise_date:
        return []
    significant = [(f, d) for f, d in changes if _is_significant(str((f.value or {}).get("content", "")))]
    if not significant:
        return []
    fact, doc = significant[0]
    titles = [str((f.value or {}).get("content", ""))[:80] for f, _ in significant[:3]]
    return [Detection(
        code="D-13",
        title=f"В проектную документацию после положительного заключения экспертизы от "
              f"{expertise_date:%d.%m.%Y} внесено {len(significant)} изменений, затрагивающих "
              "характеристики надёжности и безопасности",
        evidence=[Evidence.from_fact(f, d, "факт", str((f.value or {}).get("content", ""))[:150])
                  for f, d in significant[:3]],
        element={"name": "Объект в целом", "mark": "—", "level": "—", "axes": "—", "section": "—"},
        confidence=0.95, norm_refs=["ГрК РФ:49 ч.3.8"],
        field_check="Проверить наличие заключения повторной экспертизы либо решения о том, "
                    "что изменения не затрагивают характеристики надёжности и безопасности",
        documents_to_request=["Заключение повторной государственной экспертизы",
                              "Решение застройщика о внесении изменений",
                              "Ведомость изменений проектной документации"],
        detector="expertise.repeat_required")]


_SIGNIFICANT_WORDS = ("класс", "марк", "толщин", "отметк", "арматур", "нагрузк", "сечени",
                      "несущ", "конструкц", "лестниц", "фундамент")


def _is_significant(content: str) -> bool:
    low = content.lower()
    return any(word in low for word in _SIGNIFICANT_WORDS)


def check_remarks_addressed(before: DocumentSet | None, after: DocumentSet | None,
                            ctx) -> list[Detection]:
    """Замечание экспертизы не учтено в проектной документации (переход T2)."""
    expertise = after if after and after.of_kind(DocKind.EXPERTISE) else before
    project = before if expertise is after else after
    if expertise is None or project is None:
        return []
    remarks = [(f, d) for d in expertise.of_kind(DocKind.EXPERTISE)
               for f in d.facts_of("expertise_remark")]
    if not remarks:
        return []
    project_text = " ".join(str(f.value) for d in project.of_kind(DocKind.PD)
                            for f in d.facts).lower()
    found: list[Detection] = []
    for fact, doc in remarks:
        value = fact.value or {}
        resolution = str(value.get("resolution", "")).strip().lower()
        if resolution and not any(m in resolution for m in UNRESOLVED_MARKERS):
            continue
        text = str(value.get("text", ""))
        if _mentioned(text, project_text):
            continue
        found.append(Detection(
            code="D-15",
            title=f"Замечание экспертизы № {value.get('n', '')} не отражено в проектной "
                  f"документации: {text[:120]}",
            evidence=[Evidence.from_fact(fact, doc, "требование", text[:150])],
            element={"name": "Объект в целом", "mark": "—", "level": "—", "axes": "—",
                     "section": "—"},
            confidence=0.86, norm_refs=["ГрК РФ:49 ч.3.8"],
            field_check="Запросить у застройщика сведения об учёте замечания экспертизы",
            documents_to_request=["Ответ проектной организации на замечания экспертизы",
                                  "Изменённые листы проектной документации"],
            detector="expertise.remark_not_addressed"))
    return found


def _mentioned(remark_text: str, project_text: str) -> bool:
    """Отражено ли содержание замечания в проектной документации."""
    words = {w.strip(".,;:()").lower() for w in remark_text.split() if len(w) > 6}
    words -= STOP_WORDS
    if not words:
        return False
    hits = sum(1 for w in words if w in project_text)
    return hits / len(words) >= 0.6
