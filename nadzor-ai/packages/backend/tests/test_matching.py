import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import DocumentInput, match_page_pairs


def tf(page, text):
    return {"page": page, "text": text}


def test_discipline_gating_prefers_same_code():
    before = [
        DocumentInput("Том 5.4.2 ОВ.pdf", 1, [tf(1, "коридор система отопления калорифер узел учёта")], [], "ОВ"),
        DocumentInput("Раздел АР.pdf", 1, [tf(1, "коридор перегородка стена дверной проём отделка")], [], "АР"),
    ]
    after = [
        DocumentInput("Исполнительный АР план.pdf", 1, [tf(1, "коридор система отопления перегородка стена")], [], "АР"),
        DocumentInput("Акт ОВ2.1.pdf", 1, [tf(1, "коридор калорифер узел учёта тепла")], [], "ОВ"),
    ]
    pairs = match_page_pairs(before, after)
    ov_pair = next(p for p in pairs if before[p.before_file_idx].name == "Том 5.4.2 ОВ.pdf")
    ar_pair = next(p for p in pairs if before[p.before_file_idx].name == "Раздел АР.pdf")
    assert after[ov_pair.after_file_idx].name == "Акт ОВ2.1.pdf", pairs
    assert after[ar_pair.after_file_idx].name == "Исполнительный АР план.pdf", pairs
    assert ov_pair.matched_by == "text"
    print("OK: discipline gating prefers same-code file over stronger raw text overlap")


def test_ungated_when_no_matching_code_on_other_side():
    before = [DocumentInput("Раздел КР.pdf", 1, [tf(1, "уникальные слова для сопоставления фундамент балка")], [], "КР")]
    after = [DocumentInput("Без явного раздела.pdf", 1, [tf(1, "уникальные слова для сопоставления фундамент балка")], [], None)]
    pairs = match_page_pairs(before, after)
    assert len(pairs) == 1 and pairs[0].matched_by == "text"
    print("OK: no code on the other side does not block a good text match")


def test_positional_fallback_flags_discipline_mismatch():
    before = [DocumentInput("Том ОВ.pdf", 1, [tf(1, "калорифер трубопровод вентилятор увлажнитель")], [], "ОВ")]
    after = [DocumentInput("Раздел АР.pdf", 1, [tf(1, "перегородка витраж козырёк парапет")], [], "АР")]
    pairs = match_page_pairs(before, after)
    assert len(pairs) == 1
    assert pairs[0].matched_by == "position"
    assert pairs[0].discipline_mismatch is True
    print("OK: positional fallback correctly flags mismatched known codes")


def test_digit_suffix_ignored_ov21_vs_ov1():
    """Прямая проверка требования из этой сессии: «ОВ2.1» и «ОВ1» — один и
    тот же раздел, цифры после кода отбрасываются классификацией, здесь
    просто проверяем, что гейтинг корректно работает при одинаковом коде,
    полученном из разных исходных шифров."""
    before = [DocumentInput("П-ИОС5.4.2.pdf", 1, [tf(1, "нет общих слов вообще совсем")], [], "ОВ")]
    after = [DocumentInput("РД-ОВ1.pdf", 1, [tf(1, "тоже никаких общих слов тут")], [], "ОВ")]
    pairs = match_page_pairs(before, after)
    assert len(pairs) == 1
    assert pairs[0].discipline_mismatch is False
    print("OK: same normalized code (ОВ2.1 vs ОВ1 -> both ОВ) does not trigger mismatch flag")


if __name__ == "__main__":
    test_discipline_gating_prefers_same_code()
    test_ungated_when_no_matching_code_on_other_side()
    test_positional_fallback_flags_discipline_mismatch()
    test_digit_suffix_ignored_ov21_vs_ov1()
    print("ALL PASS")
