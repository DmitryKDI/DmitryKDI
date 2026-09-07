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


def test_extract_keeps_requirement_without_rooms():
    """Г.86 — ОБРАТНОЕ прежнему поведению, и это намеренно. Раньше здесь
    проверялось, что требование без помещения отбрасывается: тогда модуль
    питал только автоматическую сверку с РД по номеру помещения, которой
    пустой `rooms` бесполезен. Теперь это главный путь извлечения, и такой
    отброс терял почти весь результат на разделах, где требования по своей
    природе относятся к объекту целиком (ООС, ПОС, ПБ). Фильтрует тот, кому
    нужны именно привязанные к помещению, — на своей стороне."""
    facts = [tf(1, "текст")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [{"rooms": [], "code": None, "requirement": "общее указание без помещения", "sentence": "..."}]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert len(reqs) == 1 and reqs[0].rooms == []
    print("OK: требование к объекту целиком сохраняется, а не отбрасывается")


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


def test_on_chunk_error_callback_fires_with_page_and_exception():
    """Г.77 — реальный найденный пробел: до этого колбэка сбой ВСЕХ пачек
    (например, системная SSL-ошибка сертификата GigaChat, реально
    наблюдённая на живом прогоне) давал честный, но НЕВИДИМЫЙ пустой
    список — снаружи неотличимо от «в документе действительно нет
    требований». Колбэк даёт вызывающему коду шанс показать это явно."""
    facts = [tf(5, "a" * 4000), tf(9, "b" * 4000)]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        raise ConnectionError("сеть недоступна")

    errors = []
    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(
            facts, config=None, max_chars_per_call=6000,
            on_chunk_error=lambda page, exc: errors.append((page, exc)),
        )
    finally:
        requirement_llm_extract.call_llm_json = original

    assert reqs == []
    assert [page for page, _ in errors] == [5, 9]
    assert all(isinstance(exc, ConnectionError) for _, exc in errors)
    print("OK: сбой каждой пачки виден вызывающему коду через колбэк, а не только пустым списком")


def test_requirement_without_room_is_kept_not_dropped():
    """Г.86 — отбраковка требований без помещения снята. Г.83 сделал её
    видимой, но оставил: тогда модуль питал только сверку по номеру
    помещения. Теперь это ГЛАВНЫЙ путь извлечения, и выбрасывать требование
    за то, что оно относится к объекту целиком, значит терять почти весь
    результат на разделах ООС/ПОС/ПБ, где требования по своей природе не
    привязаны к помещению."""
    facts = [{"page": 3, "text": "текст", "document": "ООС8.1.pdf", "section": "ООС"}]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [
            {"rooms": [], "sentence": "Вывоз отходов по договору со спецорганизацией.", "page": 3},
            {"rooms": [], "sentence": "Предусмотрены шумозащитные экраны.", "page": 3},
            {"rooms": ["12"], "sentence": "В пом. 12 предусмотреть вытяжку.", "page": 3},
        ]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert len(reqs) == 3, "все три требования должны остаться, включая два без помещения"
    assert [r.rooms for r in reqs] == [[], [], ["12"]]
    print("OK: требование без помещения сохраняется, а не отбрасывается")


def test_requirement_carries_document_and_section():
    """Г.86 — «требования должны быть уже привязаны к разделам... инспектор
    просто видит, на какой странице требование». Без имени файла номер
    страницы бессмыслен на комплекте: нумерация в каждом томе своя."""
    facts = [{"page": 7, "text": "текст", "document": "Том ООС8.1.pdf", "section": "ООС"}]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"requirements": [{"rooms": [], "sentence": "Шумозащита предусмотрена.", "page": 7}]}

    original = _patch(requirement_llm_extract, "call_llm_json", fake_call_llm_json)
    try:
        reqs = extract_requirements_llm(facts, config=None)
    finally:
        requirement_llm_extract.call_llm_json = original

    assert reqs[0].document == "Том ООС8.1.pdf"
    assert reqs[0].section == "ООС"
    assert reqs[0].page == 7
    print("OK: требование несёт раздел, файл и страницу")


def test_chunks_never_span_two_documents():
    """Г.86 — пачка не пересекает границу документа: иначе модель увидит две
    страницы с одним номером (нумерация в каждом томе начинается заново) и
    требование нельзя будет привязать к файлу."""
    facts = [
        {"page": 1, "text": "a" * 100, "document": "том1.pdf", "section": "ООС"},
        {"page": 2, "text": "b" * 100, "document": "том1.pdf", "section": "ООС"},
        {"page": 1, "text": "c" * 100, "document": "том2.pdf", "section": "АР"},
    ]
    chunks = requirement_llm_extract._chunk_text_facts(facts, max_chars=100000)
    assert len(chunks) == 2, f"ожидалось 2 пачки по числу документов, получено {len(chunks)}"
    assert {f["document"] for f in chunks[0]} == {"том1.pdf"}
    assert {f["document"] for f in chunks[1]} == {"том2.pdf"}
    print("OK: пачка не смешивает документы")


if __name__ == "__main__":
    test_system_prompt_carries_known_violations_block()
    test_system_prompt_valid_json_schema_after_substitution()
    test_chunk_text_facts_respects_char_budget()
    test_chunk_text_facts_groups_small_pages_together()
    test_extract_parses_requirement_with_different_room_marker_and_verb()
    test_extract_parses_requirement_stated_as_table_row_not_list()
    test_extract_keeps_requirement_without_rooms()
    test_extract_uses_chunk_first_page_when_model_omits_page()
    test_extract_empty_result_when_model_finds_nothing()
    test_extract_one_chunk_failure_does_not_lose_other_chunks()
    test_on_chunk_error_callback_fires_with_page_and_exception()
    test_requirement_without_room_is_kept_not_dropped()
    test_requirement_carries_document_and_section()
    test_chunks_never_span_two_documents()
    print("ALL PASS")
