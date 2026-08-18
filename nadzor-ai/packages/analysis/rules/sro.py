"""Правило действительности допуска саморегулируемой организации."""
from __future__ import annotations

from analysis.models import Detection, DocumentSet, Evidence
from analysis.rules.collect import act_element
from analysis.rules.common import parse_ru_date, rule_enabled
from documents.schemas import DocKind


def check_sro_membership(project: DocumentSet | None, as_built: DocumentSet | None,
                         ctx) -> list[Detection]:
    """Работы выполнены после прекращения членства подрядчика в СРО.

    Все акты за пределами срока сводятся в одну находку: инспектору нужен
    период и подтверждающий документ, а не десяток одинаковых записей.
    """
    if as_built is None or not rule_enabled(ctx.rules_config, "sro_membership_expired"):
        return []
    extracts = as_built.of_kind(DocKind.SRO_EXTRACT)
    if not extracts:
        return []
    extract = extracts[0]
    valid_until = parse_ru_date(extract.value("sro_valid_until"))
    if valid_until is None:
        return []

    late = []
    for doc in as_built.of_kind(DocKind.AOSR):
        act_date = parse_ru_date(doc.value("act_date"))
        if act_date and act_date > valid_until:
            late.append((act_date, doc))
    if not late:
        return []

    late.sort(key=lambda p: p[0])
    first_date, first_doc = late[0]
    numbers = [d.title.split("№")[-1].strip() for _, d in late]
    return [Detection(
        code="D-14",
        title=f"Работы по акту № {numbers[0]} от {first_date:%d.%m.%Y}"
              + (f" и ещё по {len(numbers) - 1} актам" if len(numbers) > 1 else "")
              + f" выполнены после прекращения членства подрядчика в СРО "
                f"{valid_until:%d.%m.%Y}",
        evidence=[
            Evidence.from_fact(first_doc.kv()["act_date"], first_doc, "факт",
                               f"{first_date:%d.%m.%Y}"),
            Evidence.from_fact(extract.kv()["sro_valid_until"], extract, "основание",
                               extract.value("sro_valid_until")),
        ],
        element=act_element(first_doc), confidence=0.97,
        norm_refs=["ГрК РФ:52 ч.2"], legal_refs=["9.5.1/1"],
        field_check="Запросить актуальную выписку из реестра членов СРО на даты выполнения "
                    "работ; проверить наличие договора подряда с иным лицом",
        documents_to_request=["Актуальная выписка из реестра членов СРО",
                              "Договоры подряда за период " + f"с {first_date:%d.%m.%Y}",
                              f"Акты освидетельствования № {', '.join(numbers)}"],
        detector="sro.membership_expired")]
