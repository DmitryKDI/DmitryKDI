"""Перечень нормативов тома и привязка к нему требований (Г.101).

Указание пользователя: параметры (расчёты, параметры воздуха) должны
связываться с нормой, и в выводе должно быть сказано, какой НД соответствует;
в документах есть список норм, на основе которых выполнен проект.

Здесь закреплено главное ограничение честности: нормативы берутся ТОЛЬКО из
перечня самого тома, пункты внутри нормативов не извлекаются вообще, а способ
привязки («названа в требовании» против «сопоставлено по смыслу») различим,
потому что второе — гипотеза.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.norms_registry import (  # noqa: E402
    NORM_SOURCE_CITED,
    NORM_SOURCE_CITED_UNLISTED,
    NORM_SOURCE_MATCHED,
    cited_outside_the_list,
    find_norms,
    link_by_meaning,
    link_cited,
    parse_norms,
    render_norms_section,
)
from app.requirement_registry import Requirement  # noqa: E402

# Фрагмент реального перечня: форма записи взята с разобранного тома, чтобы
# тест проверял разбор той записи, которая встречается, а не удобной.
LIST_PAGE = """20. Перечень использованных нормативных документов
1 Постановление правительства Российской Федерации от 16 февраля 2008 г. №87
2 СП 60.13330.2020 (СНиП 41-01-2003) «Отопление, вентиляция и кондиционирование воздуха»
3 СП 118.13330.2012 «Общественные здания и сооружения»
4 СП 131.13330.2020 «СНиП 23-01-99* «Строительная климатология»
5 ГОСТ 3262-75* «Трубы стальные водогазопроводные»
6 ГОСТ 25129-82 «Грунтовка ГФ-021. Технические условия»
"""

# Лист содержания: тот же заголовок, но обозначений нормативов на нём нет.
TOC_PAGE = """СОДЕРЖАНИЕ ТОМА
19. Мероприятия по снижению шума 71
20. Перечень использованных нормативных документов 72
Графические материалы 73
"""


def _req(sentence, summary="", page=1):
    return Requirement(rooms=[], page=page, sentence=sentence, summary=summary)


# --- где искать перечень ----------------------------------------------------

def test_table_of_contents_is_not_mistaken_for_the_list():
    """Заголовок встречается дважды: в содержании и на самом листе перечня.
    Замер на реальном томе: на содержании обозначений 0, на перечне 25."""
    facts = [{"page": 7, "text": TOC_PAGE}, {"page": 76, "text": LIST_PAGE}]
    norms = find_norms(facts)
    assert norms, "перечень найден"
    assert all(n.page == 76 for n in norms), [n.page for n in norms]


def test_missing_list_is_reported_as_such_not_as_empty_section():
    """Г.10 — «перечня в томе нет» и «нормативы не показаны» требуют от
    инспектора разного."""
    assert find_norms([{"page": 1, "text": "Обычная проза без нормативов."}]) == []
    text = render_norms_section([], [_req("что-то")])
    assert "не найден" in text and "не выполнялась" in text


# --- разбор записи перечня --------------------------------------------------

def test_replaced_designation_in_brackets_is_an_alias_not_a_separate_norm():
    """«СП 60.13330.2020 (СНиП 41-01-2003) «Отопление…»» — один документ.
    Наивный разбор заводил из скобок отдельный норматив И отдавал ему
    наименование, а основной оставался безымянным."""
    norms = {n.designation: n for n in parse_norms(LIST_PAGE)}
    assert "СНиП 41-01-2003" not in norms, "заменённое обозначение — не отдельный документ"
    sp60 = norms["СП 60.13330.2020"]
    assert sp60.title.startswith("Отопление, вентиляция")
    assert "СНиП 41-01-2003" in sp60.aliases


def test_designation_inside_a_quoted_title_is_not_a_separate_norm():
    """«СП 131.13330.2020 «СНиП 23-01-99* «Строительная климатология»» — тоже
    один документ: обозначение внутри кавычек входит в наименование."""
    designations = [n.designation for n in parse_norms(LIST_PAGE)]
    assert "СП 131.13330.2020" in designations
    assert "СНиП 23-01-99" not in designations


def test_trailing_sentence_punctuation_does_not_break_matching():
    """Г.68 в этом модуле: захваченная точка в конце фразы давала не честный
    промах, а норматив, который в перечне ЕСТЬ, в списке отсутствующих."""
    norms = parse_norms(LIST_PAGE)
    req = _req("Воздуховоды по ГОСТ 3262-75*, монтаж по СП 60.13330.2020.")
    assert link_cited([req], norms) == []
    assert cited_outside_the_list([req], norms) == {}


# --- привязка ---------------------------------------------------------------

def test_norm_named_in_the_requirement_is_linked_without_a_model():
    norms = parse_norms(LIST_PAGE)
    req = _req("Трубы стальные водогазопроводные по ГОСТ 3262-75.")
    assert link_cited([req], norms) == [], "требование решено первой ступенью"
    assert req.norm == "ГОСТ 3262-75*"
    assert req.norm_source == NORM_SOURCE_CITED


def test_requirement_without_a_named_norm_goes_to_the_second_step():
    norms = parse_norms(LIST_PAGE)
    req = _req("Расчётная температура наружного воздуха -26 °C.")
    assert link_cited([req], norms) == [req]
    assert req.norm == "", "первая ступень ничего не выдумывает"


def test_meaning_step_picks_only_from_the_documents_own_list(monkeypatch):
    """Главное ограничение: норматива, которого нет в перечне тома, для
    модели не существует. Ответ с индексом вне списка отбрасывается."""
    from app import norms_registry

    norms = parse_norms(LIST_PAGE)
    inside = _req("Параметры микроклимата в помещениях.")
    outside = _req("Что-то другое.")

    monkeypatch.setattr(norms_registry, "call_llm_json", lambda *a, **kw: {"links": [
        {"index": 0, "norm_index": 1},
        {"index": 1, "norm_index": 999},   # норматив вне перечня
    ]})
    linked = link_by_meaning([inside, outside], norms, config=object())

    assert linked == 1
    assert inside.norm == norms[1].designation
    assert inside.norm_source == NORM_SOURCE_MATCHED
    assert outside.norm == "", "индекс вне перечня отброшен, а не сохранён"


def test_meaning_step_is_skipped_entirely_without_a_key():
    norms = parse_norms(LIST_PAGE)
    req = _req("Расчётная температура -26 °C.")
    assert link_by_meaning([req], norms, config=None) == 0
    assert req.norm == ""


def test_meaning_step_failure_leaves_the_requirement_unlinked(monkeypatch):
    """Г.77 — сбой вызова не должен превращаться в «норматива нет»."""
    from app import norms_registry

    def broken(*a, **kw):
        raise RuntimeError("связь оборвалась")

    monkeypatch.setattr(norms_registry, "call_llm_json", broken)
    seen = []
    req = _req("Расчётная температура -26 °C.")
    assert link_by_meaning([req], parse_norms(LIST_PAGE), config=object(),
                           on_error=lambda i, e: seen.append(e)) == 0
    assert req.norm == ""
    assert seen, "сбой назван, а не проглочен"


# --- полнота перечня --------------------------------------------------------

def test_norm_cited_in_the_text_but_absent_from_the_list_is_surfaced():
    """Найдено замером на реальном томе: в тексте «краской МА-15 (ГОСТ
    10503-71) по грунту ГФ-021 (ГОСТ 25129-82)», а в перечень попал только
    второй. Это не нарушение и так не называется, но молчание о нём
    неотличимо от «всё сошлось» (Г.10)."""
    norms = parse_norms(LIST_PAGE)
    req = _req("Окрашиваются краской МА-15 (ГОСТ 10503-71) по грунту ГФ-021 (ГОСТ 25129-82).",
               page=13)
    link_cited([req], norms)

    outside = cited_outside_the_list([req], norms)
    assert outside == {"ГОСТ 10503-71": [13]}, outside
    text = render_norms_section(norms, [req])
    assert "в перечне тома не значатся" in text
    # Прямое уточнение пользователя: это не ошибка, это надо учитывать.
    assert "НЕ нарушение" in text and "Учитывать при проверке" in text


def test_unlisted_norm_is_still_used_as_the_link_not_dropped():
    """Уточнение пользователя: ссылка на норму вне перечня — не ошибка. Значит
    и терять её нельзя: норматив назван проектировщиком прямым текстом, а
    перечень мог быть оформлен в другом томе комплекта. Требование получает
    ссылку с честной пометкой, а не остаётся без привязки вовсе."""
    norms = parse_norms(LIST_PAGE)
    req = _req("Регистры из бесшовных труб по ГОСТ 8732-78.", page=12)

    assert link_cited([req], norms) == [], "требование решено первой ступенью"
    assert req.norm == "ГОСТ 8732-78"
    assert req.norm_source == NORM_SOURCE_CITED_UNLISTED
    assert "в перечне тома нет" in req.norm_source


def test_norm_from_the_list_wins_over_an_unlisted_one_in_the_same_sentence():
    """Когда в предложении названы оба, ссылка идёт на объявленный в томе: по
    нему проект и обязан соответствовать. Второй при этом не теряется — он
    виден в строке «не значатся в перечне»."""
    norms = parse_norms(LIST_PAGE)
    req = _req("Краской МА-15 (ГОСТ 10503-71) по грунту ГФ-021 (ГОСТ 25129-82).", page=13)
    link_cited([req], norms)

    assert req.norm == "ГОСТ 25129-82"
    assert req.norm_source == NORM_SOURCE_CITED
    assert "ГОСТ 10503-71" in cited_outside_the_list([req], norms)


def test_report_separates_cited_from_matched():
    """Способ привязки виден в отчёте: «сопоставлено по смыслу» — гипотеза
    модели, и показывать её наравне с проверенной ссылкой нельзя."""
    norms = parse_norms(LIST_PAGE)
    cited = _req("Трубы по ГОСТ 3262-75.")
    link_cited([cited], norms)
    guessed = _req("Параметры микроклимата.")
    guessed.norm, guessed.norm_source = norms[1].designation, NORM_SOURCE_MATCHED

    text = render_norms_section(norms, [cited, guessed])
    assert "названа в тексте: 1" in text
