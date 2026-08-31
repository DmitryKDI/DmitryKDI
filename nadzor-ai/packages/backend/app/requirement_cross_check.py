"""Сверка требований ПД против корпуса РД (Приложение Г.33/Г.36 CLAUDE.md).

Второй шаг механики, которую предложил пользователь в этой сессии:
«сначала сводка из ПД по требованиям, потом в РД это сравнивается со
сводкой». Первый шаг — извлечение реестра требований из прозы ПД,
источник намеренно НЕ фиксирован здесь: этот модуль принимает уже готовый
`list[Requirement]`, откуда бы он ни взялся — `requirement_llm_extract.py`
(общий путь, ЛЛМ, работает на прозе любого формата) или
`requirement_registry.py` (узкий regex-путь без ключа ЛЛМ, см. его
докстринг). Развязка источника от сверки — прямое следствие того, что
regex-экстрактор был подобран под ОДИН конкретный документ и не обобщается
на другие форматы прозы (пользовательская поправка, Г.36); сама сверка
против РД от формата исходной прозы ПД не зависит вообще.

Требование С кодом (буквенно-числовое обозначение — код системы, марка,
позиция) сверяется присутствием этого кода как отдельного слова в полном
тексте РД. Это единственная часть механики, которая остаётся текстовым
поиском, и она уже сама по себе не зависит от формата документа: короткий
буквенно-числовой токен — общий для инженерных обозначений почти любой
дисциплины (не только ОВ), присутствие/отсутствие — да/нет по подстроке,
без предположений о грамматике или структуре списка.

Требование БЕЗ кода — по определению нечего искать текстом: ни у него, ни
у сверки нет якоря короче, чем весь текст предложения, а сверять
предложение с предложением текстовым сравнением означает заново упереться
в ту же проблему формата, от которой уходит этот модуль (было — сверка
через `extract_predicate_requirements`, запущенный и на РД; убрано этим же
изменением, потому что это тот же самый прежний regex, подобранный под
одну грамматическую форму, только на другой стороне сравнения — не
решение проблемы Г.36, а перенос той же проблемы на РД). Поэтому такое
требование ВСЕГДА получает `no_code_visual_check_needed` — прямой кандидат
на эскалацию в зрение по чертежу РД (`vision_page_compare.py`), без
попытки текстового подтверждения. Это не значит «нарушение»: требование
может быть выполнено на чертеже, просто текстом это не проверить."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .matching import DocumentInput
from .requirement_registry import Requirement


@dataclass
class RequirementFinding:
    """Находка по одному требованию из реестра ПД."""
    rooms: list[str]
    finding_type: str  # 'code_confirmed_in_rd' | 'code_missing_in_rd' | 'no_code_visual_check_needed'
    detail: str = ""
    severity: str = "существенно"
    code: Optional[str] = None
    sentence_pd: str = ""


@dataclass
class RequirementCrossCheckResult:
    """Результат сверки требований ПД↔РД."""
    findings: list[RequirementFinding] = field(default_factory=list)
    total_coded: int = 0
    total_no_code: int = 0
    coded_confirmed: int = 0
    coded_missing: int = 0


def _code_pattern(code: str) -> re.Pattern:
    return re.compile(rf"(?<![А-ЯЁ0-9]){re.escape(code)}(?![А-ЯЁ0-9])")


def _flat_text_facts(files: list[DocumentInput]) -> list[dict]:
    return [fact for entry in files for fact in entry.text_facts]


def cross_check_requirements(
    pd_requirements: list[Requirement],
    after_files: list[DocumentInput],
) -> RequirementCrossCheckResult:
    """Сверка уже извлечённого реестра требований ПД против полного
    корпуса РД. `pd_requirements` — результат любого экстрактора
    (`requirement_llm_extract.extract_requirements_llm` или
    `requirement_registry.extract_requirements`), эта функция не знает и
    не должна знать, как они были получены."""
    rd_text_facts = _flat_text_facts(after_files)
    rd_text = "\n".join(fact["text"] for fact in rd_text_facts)

    coded = [r for r in pd_requirements if r.code]
    no_code = [r for r in pd_requirements if not r.code]
    result = RequirementCrossCheckResult(total_coded=len(coded), total_no_code=len(no_code))

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
                detail=(f"{req.code}: требование из ПД (помещения: {', '.join(req.rooms)}) "
                        f"— код не найден в тексте РД"),
                severity="существенно", sentence_pd=req.sentence,
            ))

    for req in no_code:
        result.findings.append(RequirementFinding(
            rooms=req.rooms, finding_type="no_code_visual_check_needed",
            detail=(f"помещения {', '.join(req.rooms)}: требование ПД без кода — "
                    f"текстом в РД не проверяется, нужна проверка по чертежу"),
            severity="существенно", sentence_pd=req.sentence,
        ))

    return result


def render_requirement_cross_check_report(result: RequirementCrossCheckResult) -> str:
    """Печатный отчёт сверки требований."""
    lines = [
        "=== Сверка требований ПД↔РД (Г.33/Г.36) ===",
        f"Требований с кодом: {result.total_coded} "
        f"(подтверждено {result.coded_confirmed}, не найдено {result.coded_missing})",
        f"Требований без кода: {result.total_no_code} (все — кандидаты на проверку по чертежу)",
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
