"""Тесты для requirement_text_verify.py — семантическая сверка формы 3
с прозой РД (Г.49). Реального провайдера здесь нет — `call_llm_json`
подменяется фейком, как и в test_vision_page_compare.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import requirement_text_verify
from app.requirement_registry import Requirement
from app.requirement_text_verify import (
    _chunk_text_facts,
    render_text_verify_report,
    text_verify_system_prompt,
    verify_general_requirements_llm,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def req(sentence, page=1):
    return Requirement(rooms=[], page=page, sentence=sentence)


def test_system_prompt_includes_known_violations_block():
    def fake_known_violations_block(applies_to, discipline=None):
        assert applies_to == "text"
        return "\nПРИМЕР ИЗ ПРАКТИКИ: сентинел-строка.\n"

    original = _patch(requirement_text_verify, "known_violations_block", fake_known_violations_block)
    try:
        prompt = text_verify_system_prompt()
    finally:
        requirement_text_verify.known_violations_block = original
    assert "ПРИМЕР ИЗ ПРАКТИКИ" in prompt
    print("OK: блок known_violations.json (applies_to=text) подключён к промпту")


def test_system_prompt_valid_json_schema_after_substitution():
    """Регресс на ошибку экранирования (Г.37): JSON-схема ответа не должна
    пострадать от второго прохода .format() при подстановке known-блока."""
    prompt = text_verify_system_prompt()
    assert '{"verdicts": [' in prompt
    assert '"id": "R<номер требования из списка>"' in prompt
    assert '{"verdicts": []}' in prompt
    print("OK: JSON-схема вердиктов в промпте не искажена вторым проходом .format()")


def test_chunk_text_facts_respects_char_budget():
    facts = [{"page": i, "text": "x" * 100} for i in range(1, 6)]
    chunks = _chunk_text_facts(facts, max_chars=250)
    assert all(sum(len(f["text"]) for f in c) <= 250 or len(c) == 1 for c in chunks)
    assert sum(len(c) for c in chunks) == 5
    print("OK: пачки не превышают потолок символов (кроме одиночной большой страницы)")


def test_single_chunk_confirms_one_requirement():
    facts = [{"page": 10, "text": "Воздушно-тепловая завеса установлена над входом."}]
    reqs = [req("Над входом предусматривается установка завесы.")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        assert "R1" in user_text
        assert "завес" in user_text.lower()
        return {"verdicts": [{"id": "R1", "verdict": "confirmed", "reason": "завеса упомянута в тексте РД"}]}

    original = _patch(requirement_text_verify, "call_llm_json", fake_call_llm_json)
    try:
        results = verify_general_requirements_llm(reqs, facts, config=None)
    finally:
        requirement_text_verify.call_llm_json = original

    assert len(results) == 1
    assert results[0]["verdict"] == "confirmed"
    assert results[0]["chunks_checked"] == 1
    print("OK: требование получает confirmed за один вызов на одну пачку")


def test_unresolved_requirement_stays_unclear_not_dropped():
    """Требование, по которому НИ ОДНА пачка не дала вердикта, остаётся в
    результате как unclear — не исчезает из отчёта (Г.10)."""
    facts = [{"page": 1, "text": "текст, не касающийся требования вообще"}]
    reqs = [req("Экраны должны быть выполнены из негорючих материалов.")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"verdicts": []}

    original = _patch(requirement_text_verify, "call_llm_json", fake_call_llm_json)
    try:
        results = verify_general_requirements_llm(reqs, facts, config=None)
    finally:
        requirement_text_verify.call_llm_json = original

    assert len(results) == 1
    assert results[0]["verdict"] == "unclear"
    assert "не дал" in results[0]["reason"] or "не даёт" in results[0]["reason"]
    print("OK: нерешённое требование остаётся unclear, а не пропадает из отчёта")


def test_resolved_requirement_is_not_sent_to_later_chunks():
    """Как только требование получило confirmed/absent, оно НЕ должно
    попадать в список «нерешённых» на следующей пачке — экономия вызовов
    (первый небезразличный вердикт останавливает перебор для этого
    требования, тот же принцип, что vision_page_compare.check_visual_candidates).
    Второе требование остаётся нерешённым после первой пачки, поэтому
    вторая пачка всё равно обрабатывается — но уже без R1."""
    facts = [
        {"page": 1, "text": "первая пачка"},
        {"page": 2, "text": "вторая пачка"},
    ]
    reqs = [req("Требование А."), req("Требование Б.")]
    calls = []

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        calls.append(user_text)
        if len(calls) == 1:
            assert "R1" in user_text and "R2" in user_text
            return {"verdicts": [{"id": "R1", "verdict": "absent", "reason": "не сделано"}]}
        # вторая пачка: R1 уже решено и не должно снова попасть в список
        assert "R1" not in user_text and "R2" in user_text
        return {"verdicts": []}

    original = _patch(requirement_text_verify, "call_llm_json", fake_call_llm_json)
    try:
        results = verify_general_requirements_llm(reqs, facts, config=None, max_chars_per_call=5)
    finally:
        requirement_text_verify.call_llm_json = original

    assert len(calls) == 2, "обе пачки должны были обработаться"
    by_sentence = {r["sentence"]: r for r in results}
    assert by_sentence["Требование А."]["verdict"] == "absent"
    assert by_sentence["Требование А."]["chunks_checked"] == 1
    assert by_sentence["Требование Б."]["verdict"] == "unclear"
    assert by_sentence["Требование Б."]["chunks_checked"] == 2
    print("OK: решённое требование не отправляется на следующие пачки")


def test_malformed_model_response_is_ignored_not_crashed():
    """Ответ модели без разбираемых id/verdict не должен ронять сверку —
    требование просто остаётся unclear."""
    facts = [{"page": 1, "text": "текст"}]
    reqs = [req("Требование Б.")]

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"verdicts": [{"id": "не-число", "verdict": "confirmed"}, {"verdict": "absent"}]}

    original = _patch(requirement_text_verify, "call_llm_json", fake_call_llm_json)
    try:
        results = verify_general_requirements_llm(reqs, facts, config=None)
    finally:
        requirement_text_verify.call_llm_json = original

    assert results[0]["verdict"] == "unclear"
    print("OK: неразбираемый ответ модели не роняет сверку, требование остаётся unclear")


def test_on_result_callback_fires_for_each_resolved_requirement():
    facts = [{"page": 1, "text": "текст про требование В"}]
    reqs = [req("Требование В.")]
    seen = []

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"verdicts": [{"id": "R1", "verdict": "confirmed", "reason": "видно в тексте"}]}

    original = _patch(requirement_text_verify, "call_llm_json", fake_call_llm_json)
    try:
        verify_general_requirements_llm(reqs, facts, config=None, on_result=seen.append)
    finally:
        requirement_text_verify.call_llm_json = original

    assert len(seen) == 1 and seen[0]["verdict"] == "confirmed"
    print("OK: on_result вызывается по мере готовности каждого требования")


def test_render_text_verify_report_groups_by_verdict():
    results = [
        {"sentence": "А", "page": 1, "verdict": "absent", "reason": "не сделано", "chunks_checked": 1},
        {"sentence": "Б", "page": 2, "verdict": "confirmed", "reason": "сделано", "chunks_checked": 1},
        {"sentence": "В", "page": 3, "verdict": "unclear", "reason": "нет оснований", "chunks_checked": 2},
    ]
    report = render_text_verify_report(results)
    assert "Проверено требований: 3" in report
    assert "absent (1)" in report and "confirmed (1)" in report and "unclear (1)" in report
    print("OK: отчёт группирует находки по вердикту")


if __name__ == "__main__":
    test_system_prompt_includes_known_violations_block()
    test_system_prompt_valid_json_schema_after_substitution()
    test_chunk_text_facts_respects_char_budget()
    test_single_chunk_confirms_one_requirement()
    test_unresolved_requirement_stays_unclear_not_dropped()
    test_resolved_requirement_is_not_sent_to_later_chunks()
    test_malformed_model_response_is_ignored_not_crashed()
    test_on_result_callback_fires_for_each_resolved_requirement()
    test_render_text_verify_report_groups_by_verdict()
    print("ALL PASS")
