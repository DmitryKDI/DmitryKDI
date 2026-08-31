"""Сверка требований ПД против корпуса РД (Приложение Г.33 CLAUDE.md, п.41).

Второй шаг механики, которую предложил пользователь в этой сессии:
«сначала сводка из ПД по требованиям, потом в РД это сравнивается со
сводкой». Первый шаг — `requirement_registry.py` (реестр требований из
прозы ПД, две формы: с кодом системы и без кода). Здесь — сверка каждой
записи реестра против полного текстового корпуса РД, по тому же
принципу, что `room_cross_check.py`/`equip_cross_check.py`: не привязка
к конкретной паре сопоставленных листов, а весь комплект целиком.

Две формы реестра сверяются РАЗНЫМИ методами, потому что несут разный
по силе сигнал:

  - Требование С кодом системы: код ищется как отдельное слово в полном
    тексте РД. Ровно так была вручную сверена ролевая проверка списка
    противодымной защиты в этой сессии (43 из 46 кодов подтвердились
    присутствием в спецификации вентиляторов РД, «Проект: <модель>»).
    Присутствие кода в тексте РД — сигнал «система вообще фигурирует в
    РД», не сверка списка помещений (у РД нет реестра вида «код →
    список помещений» в том же текстовом виде, что в прозе ПД).

  - Требование БЕЗ кода (форма, которой было найдено нарушение №2 —
    тёплые полы): сверять по коду нечего. Единственный текстовый сигнал
    — та же форма требования (`extract_predicate_requirements`),
    запущенная и на РД, с пересечением по номерам помещений. ВАЖНО:
    отсутствие такого предложения в тексте РД — НЕ равно отсутствию на
    чертеже. У нарушения №2 требование живёт в прозе ПЗ, а
    несоответствие — на чертеже плана; текст РД мог бы вообще не
    повторять формулировку ПЗ, даже если чертёж всё показывает верно.
    Поэтому `predicate_missing_in_rd` — КАНДИДАТ на дальнейшую проверку
    (эскалация в зрение по чертежу РД, см. `escalation.py`), а не
    готовый вердикт «нарушение». Severity здесь — про то, что стоит
    проверить, не про доказанность."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .matching import DocumentInput
from .requirement_registry import extract_coded_requirements, extract_predicate_requirements


@dataclass
class RequirementFinding:
    """Находка по одному требованию из реестра ПД."""
    rooms: list[str]
    finding_type: str  # 'code_confirmed_in_rd' | 'code_missing_in_rd' | 'predicate_confirmed_in_rd' | 'predicate_missing_in_rd'
    detail: str = ""
    severity: str = "существенно"
    code: Optional[str] = None
    sentence_pd: str = ""


@dataclass
class RequirementCrossCheckResult:
    """Результат сверки требований ПД↔РД."""
    findings: list[RequirementFinding] = field(default_factory=list)
    total_coded: int = 0
    total_predicate: int = 0
    coded_confirmed: int = 0
    coded_missing: int = 0
    predicate_confirmed: int = 0
    predicate_missing: int = 0


def _code_pattern(code: str) -> re.Pattern:
    return re.compile(rf"(?<![А-ЯЁ0-9]){re.escape(code)}(?![А-ЯЁ0-9])")


def _flat_text_facts(files: list[DocumentInput]) -> list[dict]:
    return [fact for entry in files for fact in entry.text_facts]


def cross_check_requirements(
    before_files: list[DocumentInput],
    after_files: list[DocumentInput],
) -> RequirementCrossCheckResult:
    """Сверка реестра требований ПД (обе формы) против полного корпуса РД."""
    pd_text_facts = _flat_text_facts(before_files)
    rd_text_facts = _flat_text_facts(after_files)

    coded = extract_coded_requirements(pd_text_facts)
    predicate = extract_predicate_requirements(pd_text_facts)
    rd_predicate = extract_predicate_requirements(rd_text_facts)
    rd_text = "\n".join(fact["text"] for fact in rd_text_facts)

    result = RequirementCrossCheckResult(total_coded=len(coded), total_predicate=len(predicate))

    seen_codes: set[str] = set()
    for req in coded:
        if req.code in seen_codes:
            continue
        seen_codes.add(req.code)
        if _code_pattern(req.code).search(rd_text):
            result.coded_confirmed += 1
            result.findings.append(RequirementFinding(
                rooms=req.rooms, finding_type="code_confirmed_in_rd", code=req.code,
                detail=f"{req.code}: код присутствует в тексте РД",
                severity="незначительно", sentence_pd=req.sentence,
            ))
        else:
            result.coded_missing += 1
            result.findings.append(RequirementFinding(
                rooms=req.rooms, finding_type="code_missing_in_rd", code=req.code,
                detail=(f"{req.code}: требование из ПД (пом. {', '.join(req.rooms)}) "
                        f"— код не найден в тексте РД"),
                severity="существенно", sentence_pd=req.sentence,
            ))

    rd_predicate_rooms = [set(r.rooms) for r in rd_predicate]
    for req in predicate:
        pd_rooms = set(req.rooms)
        if any(pd_rooms & rd_rooms for rd_rooms in rd_predicate_rooms):
            result.predicate_confirmed += 1
            result.findings.append(RequirementFinding(
                rooms=req.rooms, finding_type="predicate_confirmed_in_rd",
                detail=f"пом. {', '.join(req.rooms)}: то же требование повторено в тексте РД",
                severity="незначительно", sentence_pd=req.sentence,
            ))
        else:
            result.predicate_missing += 1
            result.findings.append(RequirementFinding(
                rooms=req.rooms, finding_type="predicate_missing_in_rd",
                detail=(f"пом. {', '.join(req.rooms)}: требование ПД без кода системы "
                        f"не повторено в тексте РД — кандидат, нужна проверка по чертежу "
                        f"(текстовое отсутствие в РД не равно отсутствию на чертеже)"),
                severity="существенно", sentence_pd=req.sentence,
            ))

    return result


def render_requirement_cross_check_report(result: RequirementCrossCheckResult) -> str:
    """Печатный отчёт сверки требований."""
    lines = [
        "=== Сверка требований ПД↔РД (Г.33) ===",
        f"Требований с кодом системы: {result.total_coded} "
        f"(подтверждено {result.coded_confirmed}, не найдено {result.coded_missing})",
        f"Требований без кода: {result.total_predicate} "
        f"(подтверждено {result.predicate_confirmed}, кандидатов {result.predicate_missing})",
        f"Находки: {len(result.findings)}",
    ]

    by_type: dict[str, list[RequirementFinding]] = {}
    for f in result.findings:
        by_type.setdefault(f.finding_type, []).append(f)

    for ftype, findings in sorted(by_type.items()):
        lines.append(f"\n--- {ftype} ({len(findings)}) ---")
        for f in findings[:10]:
            lines.append(f"  [{f.severity}] {f.detail}")
        if len(findings) > 10:
            lines.append(f"  ... и ещё {len(findings) - 10}")

    return "\n".join(lines)
