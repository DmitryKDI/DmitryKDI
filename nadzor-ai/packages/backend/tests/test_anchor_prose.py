import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.anchor_prose import find_anchor_in_prose


def test_finds_anchor_inside_change_paragraph():
    """Реальная форма «Ведомости изменений»: слово смены и номер помещения
    в одном предложении — это и есть находка Г.19."""
    text_facts = [{"page": 6, "text": (
        "Ведомость изменений. Изм.3, п.4: Радиатор в помещении 270 заменен "
        "и перенесен под потолок.")}]
    hits = find_anchor_in_prose(text_facts, {"270"})
    assert hits and hits[0]["page"] == 6 and hits[0]["anchor"] == "270"
    print("OK: якорь найден внутри абзаца со словом смены")


def test_ignores_anchor_without_change_word_nearby():
    """Номер помещения, упомянутый в обычном описательном тексте без слова
    смены рядом, не считается находкой — иначе якорь-в-прозе превращается в
    голый токен-свип, от которого Г.19 явно отказывается."""
    text_facts = [{"page": 3, "text": "Помещение 270 — раздевальная с санузлом для МГН, площадь 12.4 м2."}]
    assert find_anchor_in_prose(text_facts, {"270"}) == []
    print("OK: без слова смены рядом находка не заявляется")


def test_word_boundary_no_substring_match():
    """«12» не должен совпадать внутри «112» или «12.3» — иначе якорь
    ловит произвольные числа-подстроки, а не сам номер помещения."""
    text_facts = [{"page": 1, "text": "Заменен блок 112, установлен новый на отметке 12.3 м."}]
    assert find_anchor_in_prose(text_facts, {"12"}) == []
    print("OK: границы слова не дают ложных совпадений внутри других чисел")


def test_dotted_anchor_matches_literally():
    """Якорь с точкой («006.1») ищется буквально, точка не воспринимается
    регулярным выражением как «любой символ»."""
    text_facts = [{"page": 8, "text": "Исключена прокладка по пом. 006.1, трасса перенесена."}]
    hits = find_anchor_in_prose(text_facts, {"006.1", "006.2"})
    keys = {h["anchor"] for h in hits}
    assert keys == {"006.1"}
    print("OK: точка в номере помещения экранирована, соседний похожий якорь не задет")


def test_multiple_anchors_in_same_paragraph_all_reported():
    """Один абзац Ведомости изменений часто перечисляет несколько
    помещений — каждое должно попасть в отчёт отдельной записью, иначе
    находка теряется для всех, кроме первого упомянутого номера."""
    text_facts = [{"page": 6, "text": (
        "Исключена установка радиатора в пом.269, радиатор смещен. "
        "Радиатор в помещении 270 заменен и перенесен под потолок.")}]
    hits = find_anchor_in_prose(text_facts, {"269", "270", "999"})
    found = {h["anchor"] for h in hits}
    assert found == {"269", "270"}
    print("OK: несколько якорей в одном абзаце находятся все, посторонний якорь не ловится")


def test_empty_anchor_set_returns_nothing():
    assert find_anchor_in_prose([{"page": 1, "text": "заменен радиатор в помещении 5"}], set()) == []
    print("OK: без якорей поиск ничего не возвращает")


if __name__ == "__main__":
    test_finds_anchor_inside_change_paragraph()
    test_ignores_anchor_without_change_word_nearby()
    test_word_boundary_no_substring_match()
    test_dotted_anchor_matches_literally()
    test_multiple_anchors_in_same_paragraph_all_reported()
    test_empty_anchor_set_returns_nothing()
    print("ALL PASS")
