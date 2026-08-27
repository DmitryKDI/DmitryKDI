"""Все страницы второй стороны, где встречается якорь (Приложение Г.26) —
восполняет разрыв между правилом Г.6 и жадной реализацией матчинга.

Г.6 требует: лист ПД сопоставляется СО ВСЕМИ листами РД, где встречаются
его помещения. `matching.match_page_pairs` реализует другое — жадное
сопоставление, где странице достаётся не более одной лучшей пары с каждой
стороны. На реальном прогоне это дало конкретный пропуск: помещения из
«Ведомости изменений» физически нарисованы на листе РД, который ни разу не
попал ни в одну уверенную пару — жадный матчинг уже отдал соответствующую
страницу ПД другому листу. Найти нужный лист удалось только вручную, в
обход всех инструментов.

Здесь — не замена матчингу, а дополнение к нему: по номеру помещения
находятся ВСЕ страницы, где он есть в реестре, независимо от того, кому
жадный алгоритм отдал пару. Отдельно помечается, покрыта ли уже эта
страница какой-нибудь уверенной парой: непокрытые — это ровно тот
материал, который иначе не дошёл бы до разбора вообще (Г.10 — пропуск
должен быть видимым, а не молчаливым).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .matching import DocumentInput, PagePair


@dataclass
class AnchorPageRef:
    file_idx: int
    file_name: str
    page: int
    covered_by_pair: bool  # уже разбирается через какую-то уверенную пару


@dataclass
class AnchorCoverage:
    anchor: str
    refs: list[AnchorPageRef] = field(default_factory=list)

    @property
    def uncovered(self) -> list[AnchorPageRef]:
        return [r for r in self.refs if not r.covered_by_pair]


def find_anchor_pages(
    anchors: set[str],
    after_files: list[DocumentInput],
    pairs: list[PagePair],
) -> list[AnchorCoverage]:
    """Для каждого якоря — все страницы стороны «после», где он есть в
    реестре помещений, с пометкой, покрыта ли страница уверенной парой.

    `pairs` — все пары из `match_page_pairs`; покрытыми считаются страницы
    уверенных (`matched_by="text"`) пар: позиционный резерв покрытием не
    считается, у него по определению низкая уверенность (Г.10) и попадание
    нужного листа в него ничего не гарантирует."""
    covered: set[tuple[int, int]] = {
        (p.after_file_idx, p.after_page) for p in pairs if p.matched_by == "text"
    }
    result: list[AnchorCoverage] = []
    for anchor in sorted(anchors):
        coverage = AnchorCoverage(anchor=anchor)
        for file_idx, entry in enumerate(after_files):
            pages = sorted({
                f["page"] for f in entry.room_facts if f.get("key") == anchor
            })
            for page in pages:
                coverage.refs.append(AnchorPageRef(
                    file_idx=file_idx, file_name=entry.name, page=page,
                    covered_by_pair=(file_idx, page) in covered,
                ))
        if coverage.refs:
            result.append(coverage)
    return result


def render_uncovered_report(coverages: list[AnchorCoverage]) -> str:
    """Печатный список якорей, чьи страницы не попали ни в одну уверенную
    пару — то, что при работе только через `match_page_pairs` осталось бы
    вне разбора молча."""
    lines = ["# Якоря на страницах вне уверенных пар (Г.26)"]
    any_uncovered = False
    for coverage in coverages:
        uncovered = coverage.uncovered
        if not uncovered:
            continue
        any_uncovered = True
        places = ", ".join(f"{r.file_name} стр.{r.page}" for r in uncovered)
        lines.append(f"- {coverage.anchor}: {places}")
    if not any_uncovered:
        lines.append("_(все страницы с этими якорями уже покрыты уверенными парами)_")
    return "\n".join(lines)
