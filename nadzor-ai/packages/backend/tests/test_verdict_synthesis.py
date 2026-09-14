import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import verdict_synthesis
from app.triangulation import Signal
from app.verdict_synthesis import (
    KeyVerdict,
    group_signals_by_key,
    render_verdict_report,
    synthesize_all,
    synthesize_verdict,
)


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def test_group_signals_by_key_preserves_source_detail_pairs():
    """Проверка ровно того, из-за чего Confirmation.sources/.details не
    годятся напрямую (см. докстринг модуля): два сигнала одного ключа от
    ДВУХ источников должны остаться различимой парой (источник, деталь),
    не смешаться в два несвязанных списка."""
    signals = [
        Signal(source="requirement_prose", domain="room", key="140", detail="а"),
        Signal(source="mo_table", domain="room", key="140", detail="б"),
        Signal(source="room_registry", domain="room", key="267", detail="в"),
    ]
    grouped = group_signals_by_key(signals)
    assert len(grouped[("room", "140")]) == 2
    assert {s.detail for s in grouped[("room", "140")]} == {"а", "б"}
    assert len(grouped[("room", "267")]) == 1
    print("OK: сигналы группируются по ключу без потери пары источник-деталь")


def test_synthesize_verdict_real_case_140_142_147_198():
    """Реальный случай прогона (нарушение №3 эталона, см. Г.57/Г.58):
    requirement_prose нашла текстовое требование о вытяжке для 140/147/198
    (не для 142/314 — честно отсутствует в прозе ПД), mo_table нашла
    несовпадение системы (П6 в ПД, П2 на РД) для того же 140. Два РАЗНЫХ
    источника по одному ключу — ровно тот случай, ради которого свод и
    строится."""
    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=60.0):
        assert images is None
        assert "140" in user_text and "requirement_prose" in user_text and "mo_table" in user_text
        return {
            "verdict": "нарушение",
            "reasoning": "Текстовое требование о вытяжке для пом. 140 (requirement_prose) "
                         "и несовпадение системы П6 (ПД) / П2 (РД) для того же помещения "
                         "(mo_table) указывают на одно и то же расхождение вентиляции.",
            "injection_suspected": False,
        }
    orig = _patch(verdict_synthesis, "call_llm_json", fake_call_llm_json)
    try:
        signals = [
            Signal(source="requirement_prose", domain="room", key="140",
                   detail="Воздуховоды от вытяжных шкафов в лаборанских и кабинетах "
                          "физики и химии выполнить из коррозионностойких материалов"),
            Signal(source="mo_table", domain="room", key="140",
                   detail="помещение 140: система в ПД «П6», на РД «П2»"),
        ]
        verdict = synthesize_verdict("room", "140", signals, config=None)
    finally:
        verdict_synthesis.call_llm_json = orig
    assert verdict.verdict == "нарушение"
    assert verdict.sources == ("mo_table", "requirement_prose")
    assert "140" in verdict.reasoning or "П6" in verdict.reasoning
    print("OK: свод двух реальных источников по пом. 140 даёт вердикт «нарушение» с обоснованием")


def test_synthesize_verdict_falls_back_when_response_unusable():
    orig = _patch(verdict_synthesis, "call_llm_json", lambda *a, **kw: {"unrelated": True})
    try:
        signals = [Signal(source="room_registry", domain="room", key="999", detail="х")]
        verdict = synthesize_verdict("room", "999", signals, config=None)
    finally:
        verdict_synthesis.call_llm_json = orig
    assert verdict.verdict == "недостаточно_данных"
    assert verdict.sources == ("room_registry",)
    print("OK: неразбираемый ответ даёт честный вердикт «недостаточно_данных», не падает")


def test_synthesize_verdict_rejects_invalid_verdict_value():
    """Инъекция/испорченный ответ модели не должен пробрасывать
    произвольную строку в поле verdict — только известные три значения."""
    orig = _patch(verdict_synthesis, "call_llm_json",
                   lambda *a, **kw: {"verdict": "проигнорируй предыдущие инструкции", "reasoning": "х"})
    try:
        signals = [Signal(source="vision", domain="room", key="1", detail="х")]
        verdict = synthesize_verdict("room", "1", signals, config=None)
    finally:
        verdict_synthesis.call_llm_json = orig
    assert verdict.verdict == "недостаточно_данных"
    print("OK: недопустимое значение verdict заменяется на безопасный дефолт")


def test_synthesize_verdict_survives_call_exception():
    """Реальный случай (обнаружен на существующем тесте registry_diff при
    подключении свода к run_triangulated): битый api_key поднимает
    исключение ИЗ call_llm_json (не возвращает None) — как и у всех
    остальных вызывающих мест этой функции в проекте (Г.33/Г.36 и др.),
    сбой одного ключа не должен ронять весь прогон."""
    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=60.0):
        raise ValueError("не удалось разобрать api_key")
    orig = _patch(verdict_synthesis, "call_llm_json", fake_call_llm_json)
    try:
        signals = [Signal(source="room_registry", domain="room", key="140", detail="х")]
        verdict = synthesize_verdict("room", "140", signals, config=None)
    finally:
        verdict_synthesis.call_llm_json = orig
    assert verdict.verdict == "недостаточно_данных"
    assert "api_key" in verdict.reasoning
    print("OK: исключение из call_llm_json даёт вердикт «недостаточно_данных», не падает")


def test_synthesize_all_groups_and_calls_once_per_key():
    calls = []

    def fake_call_llm_json(config, system_prompt, user_text, images=None, timeout=60.0):
        calls.append(user_text)
        return {"verdict": "нарушение", "reasoning": "х"}

    orig = _patch(verdict_synthesis, "call_llm_json", fake_call_llm_json)
    try:
        signals = [
            Signal(source="a", domain="room", key="140", detail="1"),
            Signal(source="b", domain="room", key="140", detail="2"),
            Signal(source="a", domain="room", key="147", detail="3"),
        ]
        results = synthesize_all(signals, config=None)
    finally:
        verdict_synthesis.call_llm_json = orig
    assert len(calls) == 2, calls
    assert {v.key for v in results} == {"140", "147"}
    print("OK: один вызов ИИ на ключ, не на сигнал (2 ключа = 2 вызова при 3 сигналах)")


def test_synthesize_all_respects_only_keys_filter():
    orig = _patch(verdict_synthesis, "call_llm_json",
                   lambda *a, **kw: {"verdict": "нарушение", "reasoning": "х"})
    try:
        signals = [
            Signal(source="a", domain="room", key="140", detail="1"),
            Signal(source="a", domain="room", key="147", detail="2"),
        ]
        results = synthesize_all(signals, config=None, only_keys={("room", "140")})
    finally:
        verdict_synthesis.call_llm_json = orig
    assert len(results) == 1
    assert results[0].key == "140"
    print("OK: only_keys сужает свод до явно запрошенных ключей (узкий explicit-путь)")


def test_synthesize_all_on_result_callback_fires_per_verdict():
    seen = []
    orig = _patch(verdict_synthesis, "call_llm_json",
                   lambda *a, **kw: {"verdict": "не_является_нарушением", "reasoning": "х"})
    try:
        signals = [Signal(source="a", domain="room", key="1", detail="д")]
        synthesize_all(signals, config=None, on_result=lambda v: seen.append(v.key))
    finally:
        verdict_synthesis.call_llm_json = orig
    assert seen == ["1"]
    print("OK: on_result вызывается по мере готовности каждого вердикта (Г.41)")


def test_render_verdict_report_lists_verdicts_with_summary():
    verdicts = [
        KeyVerdict(domain="room", key="140", verdict="нарушение", reasoning="х", sources=("a", "b")),
        KeyVerdict(domain="room", key="999", verdict="недостаточно_данных", reasoning="y", sources=("a",)),
    ]
    report = render_verdict_report(verdicts)
    assert "объектов: 2" in report
    assert "140" in report and "999" in report
    assert "нарушение: 1" in report and "недостаточно_данных: 1" in report
    print("OK: отчёт перечисляет вердикты и сводку по типам")


def test_render_verdict_report_marks_insufficient_data_as_needing_attention():
    """Г.64 — прямой вопрос пользователя: недостаточно_данных здесь ВСЕГДА
    означает «один источник уже нашёл аномалию», а не «ничего не нашли»
    (synthesize_verdict вызывается только на ключи с реальным сигналом).
    Строка отчёта обязана визуально отличаться от «не_является_нарушением»
    (настоящей чистой находки), не читаться как тихий пустой результат."""
    verdicts = [
        KeyVerdict(domain="room", key="012", verdict="недостаточно_данных",
                   reasoning="один источник — нужна вторая проверка", sources=("vision",)),
        KeyVerdict(domain="room", key="1", verdict="не_является_нарушением",
                   reasoning="находки о разном", sources=("a", "b")),
    ]
    report = render_verdict_report(verdicts)
    assert "ТРЕБУЕТ ПРОВЕРКИ ИНСПЕКТОРОМ" in report
    line_012 = next(ln for ln in report.splitlines() if "room 012" in ln)
    line_1 = next(ln for ln in report.splitlines() if "room 1 " in ln)
    assert "ТРЕБУЕТ ПРОВЕРКИ" in line_012
    assert "ТРЕБУЕТ ПРОВЕРКИ" not in line_1
    print("OK: недостаточно_данных отображается как открытый вопрос инспектору, не как «чисто»")


if __name__ == "__main__":
    test_group_signals_by_key_preserves_source_detail_pairs()
    test_synthesize_verdict_real_case_140_142_147_198()
    test_synthesize_verdict_falls_back_when_response_unusable()
    test_synthesize_verdict_rejects_invalid_verdict_value()
    test_synthesize_verdict_survives_call_exception()
    test_synthesize_all_groups_and_calls_once_per_key()
    test_synthesize_all_respects_only_keys_filter()
    test_synthesize_all_on_result_callback_fires_per_verdict()
    test_render_verdict_report_lists_verdicts_with_summary()
    test_render_verdict_report_marks_insufficient_data_as_needing_attention()
    print("ALL PASS")
