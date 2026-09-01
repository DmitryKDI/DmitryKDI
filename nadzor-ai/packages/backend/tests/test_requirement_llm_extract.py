"""Тесты для requirement_llm_extract.py — ЛЛМ-извлечение требований,
общий механизм вместо регулярки под один документ (Г.36).

Замена вызова `call_llm_json` мокается (тот же приём, что
balance_vision.py/dimension_vision.py) — реальное суждение модели здесь не
проверяется, только то, что плумбинг (разбивка на пачки, разбор ответа,
устойчивость к сбою) работает корректно и НЕ завязан на конкретную форму
входного текста. Фикстуры этого файла НАРОЧНО используют разные форматы
относительно requirement_registry.py (другое слово для помещения, другой
глагол, без списка вообще) — это и есть проверка того, что механизм общий,
а не что он подтверждает те же регулярки другим способом."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import requirement_llm_extract
from app.requirement_llm_extract import (
    _chunk_text_facts,
    extract_requirements_llm,
    requirement_extraction_system_prompt,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def tf(page, text):
    return {"page": page, "text": text}


def test_system_prompt_carries_known_violations_block():
    """known_violations.json уже несёт общие (discipline='*') примеры —
    промпт извлечения требований обязан их включать, тем же механизмом,
    что vision_system_prompt/text_compare_system_prompt в vision.py (Г.36:
    этот модуль был написан с нуля и забыл про существующий механизм
    few-shot-примеров, что и обнаружилось при проверке)."""
    prompt = requirement_extraction_system_prompt()
    assert "хронология освидетельствования" in prompt or "материала" in prompt or "Объём работ" in prompt
    print("OK: известные нарушения из known_violations.json попадают в промпт извлечения требований")


def test_system_prompt_valid_json_schema_after_substitution():
    """Регресс на конкретную ошибку экранирования фигурных скобок при
    добавлении known_violations (KeyError на '\"requirements\"' —
    .format() принял JSON-скобки схемы за поля подстановки)."""
    prompt = requirement_extraction_system_prompt()
    assert '{"requirements": [' in prompt
    assert '{"rooms": [' in prompt
    print("OK: JSON-схема в промпте не искажена вторым проходом .format()")


def test_chunk_text_facts_respects_char_budget():
    facts = [tf(1, "a" * 100), tf(2, "b" * 100), tf(3, "c" * 100)]
    chunks = _chunk_text_facts(facts, max_chars=150)
    assert chunks == [[facts[0]], [facts[1]], [facts[2]]]
    print("OK: страницы разбиваются на пачки по потолку символов")


def test_chunk_text_facts_groups_small_pages_together():
    facts = [tf(1, "a" * 50), tf(2, "b" * 50), tf(3, "c" * 50)]
    chunks = _chunk_text_facts(facts, max_chars=150)
    assert chunks == [[facts[0], facts[1], facts[2]]]
    print("OK: маленькие страницы объединяются в одну пачку, пока есть бюджет")


def test_extract_parses_requirement_with_different_room_marker_and_verb():
    """Другое слово для помещения («каб.», не «пом.») и другой глагол
    («должна быть выполнена», не «предусмотрена») — регулярки
    requirement_registry.py такое бы не поймали, ЛЛМ-путь не должен от
    этого зависеть."""
    facts = [tf(5, "В кабинетах 12 и 14 система вентиляции должна быть выполнена с шумоглушением.")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        assert "Страница 5" in user_text
        return {"requirements": [
            {"rooms": ["12", "14"], "code": None,
             "requirement": "вентиляция с шумоглушением",
             "sentence": "система вентиляции должна быть выполнена с шумоглушением",
             "page": 5},
        ]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert len(reqs) == 1
    assert reqs[0].rooms == ["12", "14"]
    assert reqs[0].code is None
    assert reqs[0].page == 5
    print("OK: требование в незнакомой regex-форме извлекается через ЛЛМ-путь")


def test_extract_parses_requirement_stated_as_table_row_not_list():
    """Требование в виде табличной строки без единого маркера списка."""
    facts = [tf(9, "Зона А | подпор воздуха 20 Па | ПД5")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [
            {"rooms": ["Зона А"], "code": "ПД5",
             "requirement": "подпор воздуха 20 Па",
             "sentence": "Зона А | подпор воздуха 20 Па | ПД5", "page": 9},
        ]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert reqs[0].rooms == ["Зона А"]
    assert reqs[0].code == "ПД5"
    print("OK: требование из табличной строки (не список, не абзац) тоже разбирается")


def test_extract_skips_requirement_without_rooms():
    facts = [tf(1, "текст")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [{"rooms": [], "code": None, "requirement": "общее указание без помещения", "sentence": "..."}]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert reqs == []
    print("OK: требование без единого помещения не превращается в запись реестра")


def test_extract_uses_chunk_first_page_when_model_omits_page():
    facts = [tf(3, "текст")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [{"rooms": ["1"], "code": None, "requirement": "x", "sentence": "y"}]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert reqs[0].page == 3
    print("OK: без номера страницы в ответе модели берётся первая страница пачки, не падение")


def test_extract_empty_result_when_model_finds_nothing():
    facts = [tf(1, "текст без требований")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": []}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        assert extract_requirements_llm(facts, config=None) == []
    finally:
        requirement_llm_extract.call_llm_json = original
    print("OK: честный пустой результат остаётся пустым, не выдумывается требование")


def test_extract_one_chunk_failure_does_not_lose_other_chunks():
    """Сбой вызова модели на одной пачке (сеть, лимит) не должен ронять
    извлечение по остальным страницам — тот же принцип устойчивости, что
    у vision_page_compare.check_requirement_on_page."""
    facts = [tf(1, "a" * 4000), tf(2, "b" * 4000)]  # каждая страница — отдельная пачка при max_chars=6000

    calls = []

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        calls.append(user_text)
        if len(calls) == 1:
            raise ConnectionError("сеть недоступна")
        return {"requirements": [{"rooms": ["2"], "code": None, "requirement": "x", "sentence": "y", "page": 2}]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None, max_chars_per_call=6000)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert len(calls) == 2
    assert len(reqs) == 1 and reqs[0].rooms == ["2"]
    print("OK: сбой одной пачки не теряет требования из остальных пачек")


if __name__ == "__main__":
    test_system_prompt_carries_known_violations_block()
    test_system_prompt_valid_json_schema_after_substitution()
    test_chunk_text_facts_respects_char_budget()
    test_chunk_text_facts_groups_small_pages_together()
    test_extract_parses_requirement_with_different_room_marker_and_verb()
    test_extract_parses_requirement_stated_as_table_row_not_list()
    test_extract_skips_requirement_without_rooms()
    test_extract_uses_chunk_first_page_when_model_omits_page()
    test_extract_empty_result_when_model_finds_nothing()
    test_extract_one_chunk_failure_does_not_lose_other_chunks()
    print("ALL PASS")
