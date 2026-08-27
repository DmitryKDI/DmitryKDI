import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.anchor_pages import find_anchor_pages, render_uncovered_report
from app.matching import DocumentInput, PagePair


def _after(name, room_facts):
    return DocumentInput(name=name, pages=100, room_facts=room_facts, discipline_code="ОВ")


def test_finds_all_pages_with_anchor_not_just_the_paired_one():
    """Суть Г.26: помещение нарисовано на нескольких листах РД, а жадный
    матчинг отдаёт паре только один из них — остальные должны находиться."""
    after = [_after("РД-ОВ2.1", [
        {"page": 16, "key": "270"}, {"page": 17, "key": "270"}, {"page": 18, "key": "270"},
    ])]
    pairs = [PagePair(0, 99, 0, 16, 0.26, "text", "drawing")]
    coverage = find_anchor_pages({"270"}, after, pairs)[0]
    assert [r.page for r in coverage.refs] == [16, 17, 18]
    assert [r.page for r in coverage.uncovered] == [17, 18]
    print("OK: найдены все страницы с якорем, непокрытые парами выделены отдельно")


def test_positional_pairs_do_not_count_as_coverage():
    """Позиционный резерв — низкая уверенность по определению (Г.10);
    попадание нужного листа в него не означает, что лист реально разобран."""
    after = [_after("РД-ОВ1", [{"page": 20, "key": "140"}])]
    pairs = [PagePair(0, 88, 0, 20, 0.0, "position", "drawing")]
    coverage = find_anchor_pages({"140"}, after, pairs)[0]
    assert coverage.uncovered and coverage.uncovered[0].page == 20
    print("OK: позиционная пара не засчитывается как покрытие")


def test_anchor_absent_on_after_side_is_omitted():
    after = [_after("РД-ОВ1", [{"page": 20, "key": "140"}])]
    assert find_anchor_pages({"999"}, after, []) == []
    print("OK: якорь, которого нет на стороне «после», не порождает пустую запись")


def test_multiple_files_are_searched_and_named():
    """РД часто разбита на несколько файлов — искать надо во всех, и в
    отчёте различать, в каком именно файле лежит страница."""
    after = [
        _after("РД-ОВ1 ч.1", [{"page": 20, "key": "314"}]),
        _after("РД-ОВ1 ч.2", [{"page": 575, "key": "314"}]),
    ]
    coverage = find_anchor_pages({"314"}, after, [])[0]
    names = {(r.file_name, r.page) for r in coverage.refs}
    assert names == {("РД-ОВ1 ч.1", 20), ("РД-ОВ1 ч.2", 575)}
    print("OK: поиск идёт по всем файлам стороны «после», имя файла сохраняется")


def test_render_report_lists_only_uncovered():
    after = [_after("РД-ОВ2.1", [{"page": 16, "key": "270"}, {"page": 17, "key": "270"}])]
    pairs = [PagePair(0, 99, 0, 16, 0.26, "text", "drawing")]
    report = render_uncovered_report(find_anchor_pages({"270"}, after, pairs))
    assert "стр.17" in report
    assert "стр.16" not in report, "покрытая парой страница не должна попадать в отчёт о пропусках"
    print("OK: отчёт показывает только то, что иначе осталось бы вне разбора")


def test_render_report_when_everything_covered():
    after = [_after("РД-ОВ2.1", [{"page": 16, "key": "270"}])]
    pairs = [PagePair(0, 99, 0, 16, 0.26, "text", "drawing")]
    report = render_uncovered_report(find_anchor_pages({"270"}, after, pairs))
    assert "уже покрыты" in report
    print("OK: при полном покрытии отчёт явно это говорит, а не остаётся пустым")


if __name__ == "__main__":
    test_finds_all_pages_with_anchor_not_just_the_paired_one()
    test_positional_pairs_do_not_count_as_coverage()
    test_anchor_absent_on_after_side_is_omitted()
    test_multiple_files_are_searched_and_named()
    test_render_report_lists_only_uncovered()
    test_render_report_when_everything_covered()
    print("ALL PASS")
