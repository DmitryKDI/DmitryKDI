"""Накапливаемый профиль раздела документации (Г.105).

Указание пользователя: код общий и идёт по томам одинаково, а знание о
разделах — конкретные обороты, нормативы, типы таблиц — накапливается базой
по ходу эксплуатации, чтобы следующие тома того же раздела разбирались лучше.

Проверяется механизм, а не какой-либо раздел: в тестах используются
вымышленные коды разделов и вымышленные обороты — иначе тест сам стал бы
местом, где живёт конкретика (раздел 0, п.8).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from app import section_profile as sp  # noqa: E402


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    """Свой файл профиля на каждый тест: накопление — состояние, и общий файл
    сделал бы тесты зависимыми от порядка запуска."""
    monkeypatch.setattr(sp, "STORE_PATH", str(tmp_path / "profiles.db"))
    yield


def test_accumulates_across_volumes_and_counts_documents_not_occurrences():
    """Единица счёта — ТОМ. Оборот, встреченный сто раз в одном томе, это
    по-прежнему одно наблюдение одного автора (Г.21)."""
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["оборот первый"] * 5, document="том-1")
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["оборот первый"], document="том-2")

    rows = sp.profile("РАЗДЕЛ-А", kind=sp.KIND_TERM)
    assert len(rows) == 1
    assert rows[0].documents == 2, "два разных тома"
    assert rows[0].occurrences == 6, "вхождений больше, но n считается по томам"


def test_reparsing_the_same_volume_does_not_inflate_confidence():
    """Иначе «уверенность» росла бы от перезапусков, а не от новых данных."""
    for _ in range(3):
        sp.observe("РАЗДЕЛ-А", sp.KIND_NORM, ["ОБОЗНАЧЕНИЕ 00-00"], document="том-1")
    assert sp.profile("РАЗДЕЛ-А", kind=sp.KIND_NORM)[0].documents == 1
    assert sp.documents_seen("РАЗДЕЛ-А") == 1


def test_sections_do_not_leak_into_each_other():
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["только в А"], document="том-1")
    sp.observe("РАЗДЕЛ-Б", sp.KIND_TERM, ["только в Б"], document="том-2")

    assert [r.display for r in sp.profile("РАЗДЕЛ-А")] == ["только в А"]
    assert [r.display for r in sp.profile("РАЗДЕЛ-Б")] == ["только в Б"]


def test_unknown_section_is_its_own_profile_not_a_dump():
    """«Раздел не определён» — тоже состояние, и смешивать его с любым
    определённым разделом значит портить оба (Г.10)."""
    sp.observe(None, sp.KIND_TERM, ["оборот"], document="том-1")
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["оборот"], document="том-2")

    assert sp.documents_seen(None) == 1
    assert sp.documents_seen("РАЗДЕЛ-А") == 1


def test_document_name_is_not_stored_only_its_digest():
    """Имя тома — реквизит объекта, которому не место в хранимых данных
    (Г.12/раздел 0 п.8). Для идемпотентности достаточно отпечатка."""
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["оборот"], document="секретное-имя-тома.pdf")

    with sp._session() as db:
        stored = " ".join(
            f"{row.document_digest}" for row in db.query(sp.Sighting).all())
    assert "секретное" not in stored and ".pdf" not in stored


# --- подсказка в промпт -----------------------------------------------------

def test_hint_needs_confirmation_by_a_second_volume():
    """Оборот из одного тома — привычка одного автора, а не язык раздела.
    Подсказка начинает работать со второго тома (Г.21)."""
    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["спорный оборот"], document="том-1")
    assert sp.hint_block("РАЗДЕЛ-А") == "", "по одному тому не подсказываем"

    sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["спорный оборот"], document="том-2")
    hint = sp.hint_block("РАЗДЕЛ-А")
    assert "спорный оборот" in hint


def test_hint_says_it_is_not_a_list_of_what_must_be_found():
    """Подсказка ничего не подтверждает: иначе профиль стал бы генератором
    правдоподобных находок, которых в документе нет (раздел 0, п.7)."""
    for doc in ("том-1", "том-2"):
        sp.observe("РАЗДЕЛ-А", sp.KIND_TERM, ["оборот"], document=doc)
    hint = sp.hint_block("РАЗДЕЛ-А")
    assert "НЕ список" in hint
    assert "должен быть на странице" in hint


def test_empty_profile_is_named_not_silent():
    """Г.10 — «данных пока нет» и «в разделе такого не бывает» разные вещи."""
    text = sp.render_profile("РАЗДЕЛ-А")
    assert "данных пока нет" in text.lower()
    assert "первый разобранный том" in text


def test_report_shows_how_many_volumes_back_the_knowledge():
    sp.observe("РАЗДЕЛ-А", sp.KIND_NORM, ["ОБОЗНАЧЕНИЕ 00-00"], document="том-1")
    sp.observe("РАЗДЕЛ-А", sp.KIND_NORM, ["ОБОЗНАЧЕНИЕ 00-00"], document="том-2")

    text = sp.render_profile("РАЗДЕЛ-А")
    assert "по 2 томам" in text
    assert "(2)" in text, "видно, сколькими томами подтверждено"


# --- сбор оборотов ----------------------------------------------------------

def test_terms_need_repetition_inside_the_volume():
    """Единичное сочетание слов — не оборот, а случайность: иначе профиль
    забился бы шумом с первого прогона."""
    texts = ["Уникальное сочетание встречается однажды"]
    assert sp.collect_terms(texts) == []

    repeated = ["Устойчивое сочетание слов здесь"] * sp.MIN_TERM_REPEATS
    assert any("устойчивое сочетание" in t[0] for t in sp.collect_terms(repeated))


def test_terms_skip_function_words():
    """Служебные слова оборота не образуют и одинаковы во всех разделах."""
    texts = ["Для того чтобы это было выполнено согласно"] * 5
    assert sp.collect_terms(texts) == []


def test_collector_knows_nothing_about_any_section():
    """Главное свойство: сбор одинаков для раздела, которого никто не видел."""
    made_up = ["Вымышленный термин раздела"] * 4
    terms = sp.collect_terms(made_up)
    assert terms and all(len(t[0].split()) == 2 for t in terms)
