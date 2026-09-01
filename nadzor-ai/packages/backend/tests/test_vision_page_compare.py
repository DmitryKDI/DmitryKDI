import sys
import tempfile
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import vision_page_compare
from app.requirement_cross_check import RequirementFinding
from app.vision_page_compare import (
    _candidate_pages,
    check_visual_candidates,
    check_requirement_on_page,
    render_vision_requirement_report,
    requirement_check_system_prompt,
)


def _tmp_single_page_pdf() -> str:
    doc = pymupdf.open()
    doc.new_page(width=500, height=500)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    doc.save(tmp.name)
    doc.close()
    return tmp.name


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_system_prompt_includes_known_violations_block():
    """Промпт проверки листа обязан звать known_violations_block("drawing",
    ...) — тот же механизм few-shot-примеров, что vision_system_prompt в
    vision.py (Г.36: этот модуль изначально его не подключал, забытая
    часть уже существующей механики, не новая идея)."""
    def fake_known_violations_block(applies_to, discipline=None):
        assert applies_to == "drawing"
        return "\nПРИМЕР ИЗ ПРАКТИКИ: сентинел-строка для проверки подключения.\n"

    original = _patch(vision_page_compare, "known_violations_block", fake_known_violations_block)
    try:
        prompt = requirement_check_system_prompt()
    finally:
        vision_page_compare.known_violations_block = original

    assert "ПРИМЕР ИЗ ПРАКТИКИ" in prompt
    print("OK: блок known_violations.json (applies_to=drawing) подключён к промпту")


def test_system_prompt_valid_json_schema_after_substitution():
    """Регресс на конкретную ошибку экранирования скобок: JSON-схема ответа
    не должна пострадать от второго прохода .format() при подстановке
    known-блока."""
    prompt = requirement_check_system_prompt()
    assert '{"verdict": "confirmed"|"absent"|"unclear"' in prompt
    print("OK: JSON-схема вердикта в промпте не искажена вторым проходом .format()")


def test_check_requirement_on_page_returns_model_verdict():
    path = _tmp_single_page_pdf()

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        assert images and len(images) == 1
        assert "требование" in user_text.lower()
        assert "267" in user_text
        return {"verdict": "absent", "reason": "на плане нет обогрева пола", "where": "пом. 267"}

    original = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        result = check_requirement_on_page(path, 1, "требование про подогрев полов", ["267"], config=None)
    finally:
        vision_page_compare.call_llm_json = original

    assert result["verdict"] == "absent"
    assert result["where"] == "пом. 267"
    print("OK: вердикт модели по листу прокидывается как есть")


def test_check_requirement_on_page_unclear_when_model_fails():
    path = _tmp_single_page_pdf()

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return None

    original = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        result = check_requirement_on_page(path, 1, "требование", ["1"], config=None)
    finally:
        vision_page_compare.call_llm_json = original

    assert result["verdict"] == "unclear"
    assert "не дал разбираемый ответ" in result["reason"]
    print("OK: сорванный вызов модели даёт честный unclear, а не молчаливую пустоту")


def test_check_requirement_on_page_unclear_when_model_call_raises():
    """Сетевой сбой/ошибка провайдера при вызове ИИ не должна ронять весь
    прогон (тот же принцип, что у _verify_group в registry_diff.py) —
    честный unclear с причиной, а не необработанное исключение."""
    path = _tmp_single_page_pdf()

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        raise ConnectionError("Connection reset by peer")

    original = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        result = check_requirement_on_page(path, 1, "требование", ["1"], config=None)
    finally:
        vision_page_compare.call_llm_json = original

    assert result["verdict"] == "unclear"
    assert "Connection reset by peer" in result["reason"]
    print("OK: сбой вызова ИИ даёт unclear с причиной, а не падение всего прогона")


def test_check_requirement_on_page_unclear_when_verdict_key_missing():
    path = _tmp_single_page_pdf()

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"reason": "модель не поняла формат"}

    original = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        result = check_requirement_on_page(path, 1, "требование", ["1"], config=None)
    finally:
        vision_page_compare.call_llm_json = original

    assert result["verdict"] == "unclear"
    print("OK: ответ без ключа verdict тоже не выдаётся за находку")


def test_candidate_pages_dedups_within_first_round():
    room_index = {
        "108": [{"path": "a.pdf", "page": 5}, {"path": "a.pdf", "page": 9}],
        "201": [{"path": "a.pdf", "page": 5}],  # тот же лист, что и первый вход 108 — не дублируется
        "130": [{"path": "b.pdf", "page": 1}],
    }
    pages = _candidate_pages(["108", "201", "130"], room_index, max_pages=2)
    assert pages == [{"path": "a.pdf", "page": 5}, {"path": "b.pdf", "page": 1}]
    print("OK: дубли (path, page) не повторяются, первый круг — первая страница на помещение")


def test_candidate_pages_goes_deeper_within_budget():
    """Второй круг (вторая известная страница помещения) используется,
    если бюджет ещё не исчерпан первым кругом — важно для требования с
    ОДНИМ помещением: без этого вторая попытка на другом листе того же
    помещения была бы недостижима."""
    room_index = {"270": [{"path": "a.pdf", "page": 1}, {"path": "a.pdf", "page": 2}]}
    pages = _candidate_pages(["270"], room_index, max_pages=3)
    assert pages == [{"path": "a.pdf", "page": 1}, {"path": "a.pdf", "page": 2}]
    print("OK: при свободном бюджете берётся и вторая известная страница того же помещения")


def test_candidate_pages_respects_cap():
    room_index = {str(i): [{"path": "a.pdf", "page": i}] for i in range(10)}
    pages = _candidate_pages([str(i) for i in range(10)], room_index, max_pages=3)
    assert len(pages) == 3
    print("OK: количество листов-кандидатов не превышает потолок")


def test_candidate_pages_empty_when_no_room_in_index():
    assert _candidate_pages(["999"], {}, max_pages=3) == []
    print("OK: помещение, отсутствующее в реестре РД, не даёт кандидатов")


def _rf(rooms, finding_type="no_code_visual_check_needed", sentence="требование"):
    return RequirementFinding(rooms=rooms, finding_type=finding_type, sentence_pd=sentence)


def test_check_visual_candidates_skips_other_finding_types():
    findings = [_rf(["1"], finding_type="code_missing_in_rd")]
    out = check_visual_candidates(findings, {}, config=None)
    assert out == []
    print("OK: находки с кодом системы не эскалируются в зрение этим модулем")


def test_check_visual_candidates_unclear_without_room_in_registry():
    findings = [_rf(["999"])]
    called = []

    def fake_call_llm_json(*a, **kw):
        called.append(1)
        return {"verdict": "confirmed"}

    original = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        out = check_visual_candidates(findings, {}, config=None)
    finally:
        vision_page_compare.call_llm_json = original

    assert not called, "модель не должна вызываться без единого листа-кандидата"
    assert out[0]["verdict"] == "unclear"
    assert out[0]["pages_checked"] == 0
    print("OK: требование без известного листа РД — unclear без обращения к модели")


def test_check_visual_candidates_first_decisive_verdict_wins():
    findings = [_rf(["270"])]
    room_index = {"270": [{"path": "a.pdf", "page": 1}, {"path": "a.pdf", "page": 2}]}
    calls = []

    def fake_render(path, page, max_dim=2200):
        return "data:image/png;base64,fake"

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        calls.append(1)
        if len(calls) == 1:
            return {"verdict": "unclear", "reason": "не та зона"}
        return {"verdict": "absent", "reason": "нет обогрева", "where": "пом. 270"}

    orig_render = _patch(vision_page_compare, "render_page_to_data_url", fake_render)
    orig_call = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        out = check_visual_candidates(findings, room_index, config=None)
    finally:
        vision_page_compare.render_page_to_data_url = orig_render
        vision_page_compare.call_llm_json = orig_call

    assert len(calls) == 2
    assert out[0]["verdict"] == "absent"
    assert out[0]["pages_checked"] == 2
    print("OK: первый небезразличный вердикт (не unclear) останавливает перебор листов")


def test_check_visual_candidates_all_unclear_reports_unclear():
    findings = [_rf(["270"])]
    room_index = {"270": [{"path": "a.pdf", "page": 1}]}

    def fake_render(path, page, max_dim=2200):
        return "data:image/png;base64,fake"

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=120.0):
        return {"verdict": "unclear", "reason": "лист обрезан"}

    orig_render = _patch(vision_page_compare, "render_page_to_data_url", fake_render)
    orig_call = _patch(vision_page_compare, "call_llm_json", fake_call_llm_json)
    try:
        out = check_visual_candidates(findings, room_index, config=None)
    finally:
        vision_page_compare.render_page_to_data_url = orig_render
        vision_page_compare.call_llm_json = orig_call

    assert out[0]["verdict"] == "unclear"
    assert out[0]["reason"] == "лист обрезан"
    print("OK: если ни один лист не дал решающего вердикта, находка честно остаётся unclear")


def test_render_report_groups_by_verdict():
    results = [
        {"rooms": ["270"], "verdict": "absent", "reason": "нет обогрева", "where": "пом. 270", "pages_checked": 1},
        {"rooms": ["189"], "verdict": "unclear", "reason": "не та зона", "where": "", "pages_checked": 1},
    ]
    report = render_vision_requirement_report(results)
    assert "absent (1)" in report
    assert "unclear (1)" in report
    assert "270" in report and "189" in report
    print("OK: отчёт группирует находки по вердикту")


if __name__ == "__main__":
    test_system_prompt_includes_known_violations_block()
    test_system_prompt_valid_json_schema_after_substitution()
    test_check_requirement_on_page_returns_model_verdict()
    test_check_requirement_on_page_unclear_when_model_fails()
    test_check_requirement_on_page_unclear_when_model_call_raises()
    test_check_requirement_on_page_unclear_when_verdict_key_missing()
    test_candidate_pages_dedups_within_first_round()
    test_candidate_pages_goes_deeper_within_budget()
    test_candidate_pages_respects_cap()
    test_candidate_pages_empty_when_no_room_in_index()
    test_check_visual_candidates_skips_other_finding_types()
    test_check_visual_candidates_unclear_without_room_in_registry()
    test_check_visual_candidates_first_decisive_verdict_wins()
    test_check_visual_candidates_all_unclear_reports_unclear()
    test_render_report_groups_by_verdict()
    print("ALL PASS")
