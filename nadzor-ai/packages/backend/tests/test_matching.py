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


def test_every_page_covered_when_after_side_much_larger():
    """Реальный случай пользователя: ПД 177 страниц (единственный файл),
    РД/ИД 36+676=712 страниц в двух файлах. Раньше позиционный резерв
    ограничивался min(177, 712)=177 парами, и ~535 листов РД оставались
    вообще без пары и без визуальной проверки — находка этой сессии."""
    before = [DocumentInput("pd.pdf", 177, [], [], "ОВ")]
    after = [
        DocumentInput("rd_small.pdf", 36, [], [], "ОВ"),
        DocumentInput("rd_big.pdf", 676, [], [], "ОВ"),
    ]
    # Пустые text_facts -> текстового сопоставления не будет вообще (нет
    # токенов), всё уйдёт в позиционный резерв — воспроизводит худший случай.
    pairs = match_page_pairs(before, after)

    after_pages_covered = {(p.after_file_idx, p.after_page) for p in pairs}
    expected_after_pages = {(0, p) for p in range(1, 37)} | {(1, p) for p in range(1, 677)}
    missing = expected_after_pages - after_pages_covered
    assert not missing, f"{len(missing)} after-pages got no pair at all (the exact bug this fixes): {sorted(missing)[:5]}..."
    assert len(pairs) == 712, f"expected 712 pairs (one per after-page), got {len(pairs)}"
    print(f"OK: all {len(after_pages_covered)} after-side pages covered (before: only 177 of 712 were)")


def test_drawing_and_text_pages_never_cross_paired():
    """Даже если у чертежа и текстового приложения совпадает лексика (общие
    слова раздела), сравнивать их визуально бессмысленно — это разные типы
    листов. Каждая сторона должна остаться внутри своего пула."""
    before = [DocumentInput(
        "pd.pdf", 2,
        [{"page": 1, "text": "система отопления вентиляция калорифер узел учёта"},
         {"page": 2, "text": "система отопления вентиляция калорифер узел учёта"}],
        [], "ОВ",
        page_kinds={1: "drawing", 2: "text"},
    )]
    after = [DocumentInput(
        "rd.pdf", 2,
        [{"page": 1, "text": "система отопления вентиляция калорифер узел учёта"},
         {"page": 2, "text": "система отопления вентиляция калорифер узел учёта"}],
        [], "ОВ",
        page_kinds={1: "drawing", 2: "text"},
    )]
    pairs = match_page_pairs(before, after)
    assert len(pairs) == 2, pairs
    for p in pairs:
        # чертёж (p1) должен остаться сопоставлен с чертежом, текст (p2) с текстом
        assert p.before_page == p.after_page == (1 if p.page_kind == "drawing" else 2), p
    print("OK: drawing and text pages never cross-paired even with identical vocabulary")


def test_page_kind_gating_even_when_one_side_has_no_text_pages():
    """Если у ПД нет текстовых листов вообще (только чертежи), а у РД есть и
    то, и другое — текстовые листы РД просто не с чем сравнивать, и они не
    должны утянуть на себя чертёжные листы ПД (не должно быть кросс-пар)."""
    before = [DocumentInput("pd.pdf", 1, [{"page": 1, "text": "план этажа калорифер"}], [], "ОВ",
                             page_kinds={1: "drawing"})]
    after = [DocumentInput("rd.pdf", 2,
                            [{"page": 1, "text": "план этажа калорифер"},
                             {"page": 2, "text": "содержание тома акт приложение"}],
                            [], "ОВ", page_kinds={1: "drawing", 2: "text"})]
    pairs = match_page_pairs(before, after)
    assert len(pairs) == 1, pairs
    assert pairs[0].page_kind == "drawing"
    assert pairs[0].before_page == 1 and pairs[0].after_page == 1
    print("OK: after-side text page with no before-side counterpart is left unpaired, not cross-matched to a drawing")


if __name__ == "__main__":
    test_discipline_gating_prefers_same_code()
    test_ungated_when_no_matching_code_on_other_side()
    test_positional_fallback_flags_discipline_mismatch()
    test_digit_suffix_ignored_ov21_vs_ov1()
    test_every_page_covered_when_after_side_much_larger()
    test_drawing_and_text_pages_never_cross_paired()
    test_page_kind_gating_even_when_one_side_has_no_text_pages()
    print("ALL PASS")
