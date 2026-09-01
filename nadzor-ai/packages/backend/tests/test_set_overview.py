import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import set_overview
from app.classification import ClassificationResult
from app.documents import DocumentFacts
from app.set_overview import (
    VolumeSummary,
    compare_section_coverage,
    official_section_label,
    render_section_coverage_report,
    render_volume_summary,
    summarize_set,
    summarize_volume,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_summarize_volume_reads_discipline_and_counts(monkeypatch):
    fake_classification = ClassificationResult(discipline_code="КР", source="title_page")
    fake_facts = DocumentFacts(
        name="Раздел КР.pdf", pages=40,
        text_facts=[], room_facts=[{"page": 1, "key": "1", "name": "х"}, {"page": 2, "key": "2", "name": "у"}],
        equipment_facts=[{"page": 1, "key": "М1", "name": "марка"}],
        excluded={5: "прайс поставщика"},
    )
    orig_classify = _patch(set_overview, "classify_document", lambda path, name: fake_classification)
    orig_facts = _patch(set_overview, "extract_document_facts", lambda path, name: fake_facts)
    try:
        summary = summarize_volume("Раздел КР.pdf", "Раздел КР.pdf")
    finally:
        set_overview.classify_document = orig_classify
        set_overview.extract_document_facts = orig_facts

    assert summary.discipline_code == "КР"
    assert summary.discipline_source == "title_page"
    assert summary.pages == 40
    assert summary.room_count == 2
    assert summary.equipment_count == 1
    assert summary.excluded_count == 1


def test_summarize_set_survives_a_broken_file(monkeypatch):
    def fake_classify(path, name):
        if "broken" in path:
            raise ValueError("повреждённый PDF")
        return ClassificationResult(discipline_code="АР", source="filename")

    def fake_facts(path, name):
        return DocumentFacts(name=name, pages=1, text_facts=[], room_facts=[])

    orig_classify = _patch(set_overview, "classify_document", fake_classify)
    orig_facts = _patch(set_overview, "extract_document_facts", fake_facts)
    try:
        out = summarize_set(["good.pdf", "broken.pdf"])
    finally:
        set_overview.classify_document = orig_classify
        set_overview.extract_document_facts = orig_facts

    assert len(out) == 2, "битый файл не должен уронить обзор остальных"
    assert out[0].discipline_code == "АР"
    assert out[1].discipline_code is None
    assert "ошибка чтения" in out[1].discipline_source


def test_render_volume_summary_lists_every_file():
    summaries = [
        VolumeSummary("АР.pdf", "АР.pdf", "АР", "filename", 50, 10, 0, 2),
        VolumeSummary("ОВ.pdf", "ОВ.pdf", "ОВ", "stamp_text", 30, 5, 8, 0),
    ]
    text = render_volume_summary(summaries, "ПД")
    assert "2 том(ов)" in text
    assert "АР.pdf" in text and "ОВ.pdf" in text
    assert "50 стр." in text and "30 стр." in text


def test_compare_section_coverage_three_categories():
    before = [
        VolumeSummary("ар.pdf", "ар.pdf", "АР", "filename", 1, 0, 0, 0),
        VolumeSummary("кр.pdf", "кр.pdf", "КР", "filename", 1, 0, 0, 0),
        VolumeSummary("нет_раздела.pdf", "нет_раздела.pdf", None, "none", 1, 0, 0, 0),
    ]
    after = [
        VolumeSummary("ар_рд.pdf", "ар_рд.pdf", "АР", "filename", 1, 0, 0, 0),
        VolumeSummary("эом_рд.pdf", "эом_рд.pdf", "ЭОМ", "filename", 1, 0, 0, 0),
    ]
    coverage = compare_section_coverage(before, after)
    assert coverage.both == {"АР"}
    assert coverage.only_before == {"КР"}
    assert coverage.only_after == {"ЭОМ"}
    assert coverage.undetermined_before == ["нет_раздела.pdf"]
    assert coverage.undetermined_after == []

    report = render_section_coverage_report(coverage)
    assert "АР" in report and "КР" in report and "ЭОМ" in report
    assert "нет_раздела.pdf" in report


def test_compare_section_coverage_empty_when_nothing_determined():
    coverage = compare_section_coverage([], [])
    report = render_section_coverage_report(coverage)
    assert "не определён ни для одного тома" in report


def test_official_section_label_known_and_unknown_codes():
    assert "ИОС2" in official_section_label("ОВ")
    assert "Раздел 4" in official_section_label("КР")
    assert official_section_label("ГОСЭКСПЕРТИЗА-НЕИЗВЕСТНЫЙ-КОД") == "ГОСЭКСПЕРТИЗА-НЕИЗВЕСТНЫЙ-КОД"
    assert official_section_label(None) == "раздел не определён"


def test_section_coverage_report_includes_official_labels():
    before = [VolumeSummary("ов.pdf", "ов.pdf", "ОВ", "filename", 1, 0, 0, 0)]
    after: list[VolumeSummary] = []
    report = render_section_coverage_report(compare_section_coverage(before, after))
    assert "ИОС2" in report, "отчёт должен подписывать код раздела официальным названием"


if __name__ == "__main__":
    test_summarize_volume_reads_discipline_and_counts(None)
    test_summarize_set_survives_a_broken_file(None)
    test_render_volume_summary_lists_every_file()
    test_compare_section_coverage_three_categories()
    test_compare_section_coverage_empty_when_nothing_determined()
    test_official_section_label_known_and_unknown_codes()
    test_section_coverage_report_includes_official_labels()
    print("ALL PASS")
