"""Тесты для requirement_llm_filter.py — ЛЛМ-фильтр каталога общих
требований (форма 3, Г.69), прямая идея пользователя: отправить уже
извлечённых regex-кандидатов в ЛЛМ, чтобы она решала окончательно
"требование или шум".

Замена вызова `call_llm_json` мокается (тот же приём, что
requirement_llm_extract.py/balance_vision.py) — реальное суждение модели
здесь не проверяется, только плумбинг: батчинг, разбор ответа по индексу,
устойчивость к сбою, честный рендер с видимым списком отсеянного."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import requirement_llm_filter
from app.llm import LlmConfig
from app.requirement_llm_filter import (
    _FILTER_SYSTEM_PROMPT,
    RequirementVerdict,
    _chunk_requirements,
    classify_general_requirements,
    render_general_requirements_summary_llm_filtered,
)
from app.requirement_registry import Requirement


def _patch(module, name, fake):
    original = getattr(module, name)
    setattr(module, name, fake)
    return original


def _config():
    return LlmConfig(provider="anthropic")


def req(page, sentence, rooms=None):
    return Requirement(rooms=rooms or [], page=page, sentence=sentence)


def test_system_prompt_does_not_require_obligation_wording():
    """Г.70 — прямая поправка пользователя: критерий не должен требовать
    модального глагола долженствования («должен»/«обязан»). Констатация
    факта проекта («предусмотрена X») и описание способа исполнения тоже
    обязаны считаться требованием — иначе фильтр выбросил бы ровно тот
    класс предложений, которым устроено нарушение №2 этого проекта
    (тёплые полы, `nadzor_sample`) — «предусмотрена система X», не
    «должна быть предусмотрена»."""
    assert "не суди строго по наличию модального глагола" in _FILTER_SYSTEM_PROMPT
    assert "предусмотрена" in _FILTER_SYSTEM_PROMPT
    assert "способ" in _FILTER_SYSTEM_PROMPT and "исполнения" in _FILTER_SYSTEM_PROMPT
    print("OK: промпт фильтра явно не сводит требование к повелительному наклонению")


def test_chunk_requirements_respects_batch_size():
    reqs = [req(1, f"предложение {i}") for i in range(5)]
    chunks = _chunk_requirements(reqs, batch_size=2)
    assert [len(c) for c in chunks] == [2, 2, 1]
    print("OK: кандидаты формы 3 разбиваются на пачки по batch_size")


def test_classify_marks_requirement_and_noise_by_index():
    reqs = [req(9, "Экраны должны быть негорючими."),
            req(9, "Работы выполнять в соответствии с ПУЭ.")]

    def fake_call(config, system_prompt, user_text, timeout=90.0):
        assert "1. «Экраны должны быть негорючими.»" in user_text
        assert "2. «Работы выполнять в соответствии с ПУЭ.»" in user_text
        return {"verdicts": [
            {"index": 1, "is_requirement": True, "reasoning": "обязывающее требование к объекту"},
            {"index": 2, "is_requirement": False, "reasoning": "голая ссылка на норму без содержания"},
        ]}

    original = _patch(requirement_llm_filter, "call_llm_json", fake_call)
    try:
        verdicts = classify_general_requirements(reqs, _config(), batch_size=20)
    finally:
        _patch(requirement_llm_filter, "call_llm_json", original)

    assert len(verdicts) == 2
    assert verdicts[0].is_requirement is True
    assert verdicts[1].is_requirement is False
    assert "ссылка на норму" in verdicts[1].reasoning
    print("OK: вердикт ЛЛМ сопоставляется с исходным кандидатом по индексу")


def test_classify_across_two_batches_preserves_order():
    reqs = [req(1, "первое"), req(2, "второе"), req(3, "третье")]

    def fake_call(config, system_prompt, user_text, timeout=90.0):
        # batch_size=1 — три отдельных вызова, каждый со своим единственным
        # кандидатом под индексом 1; проверяем, что три пачки не путаются
        # друг с другом и результат остаётся в исходном порядке.
        if "первое" in user_text:
            return {"verdicts": [{"index": 1, "is_requirement": True, "reasoning": "ok"}]}
        if "второе" in user_text:
            return {"verdicts": [{"index": 1, "is_requirement": True, "reasoning": "ok"}]}
        return {"verdicts": [{"index": 1, "is_requirement": False, "reasoning": "шум"}]}

    original = _patch(requirement_llm_filter, "call_llm_json", fake_call)
    try:
        verdicts = classify_general_requirements(reqs, _config(), batch_size=1)
    finally:
        _patch(requirement_llm_filter, "call_llm_json", original)

    assert [v.requirement.sentence for v in verdicts] == ["первое", "второе", "третье"]
    assert [v.is_requirement for v in verdicts] == [True, True, False]
    print("OK: результат по нескольким пачкам сохраняет исходный порядок кандидатов")


def test_batch_failure_keeps_candidates_as_requirements_not_silently_dropped():
    """Г.10 — сбой вызова (сеть/провайдер) не должен молча терять
    кандидатов: они остаются как требования с честной пометкой, что ЛЛМ
    вердикт не дала, а не исчезают из каталога."""
    reqs = [req(5, "предложение без вердикта")]

    def fake_call_raises(config, system_prompt, user_text, timeout=90.0):
        raise RuntimeError("сеть недоступна")

    original = _patch(requirement_llm_filter, "call_llm_json", fake_call_raises)
    try:
        verdicts = classify_general_requirements(reqs, _config())
    finally:
        _patch(requirement_llm_filter, "call_llm_json", original)

    assert len(verdicts) == 1
    assert verdicts[0].is_requirement is True
    assert "не дала вердикт" in verdicts[0].reasoning
    print("OK: сбой вызова не роняет фильтр и не выбрасывает кандидата молча")


def test_classify_handles_missing_index_in_response_the_same_way():
    """Модель может пропустить номер в ответе (не то же самое, что сбой
    вызова, но последствие для конкретного кандидата — то же самое)."""
    reqs = [req(1, "первое"), req(2, "второе")]

    def fake_call(config, system_prompt, user_text, timeout=90.0):
        return {"verdicts": [{"index": 1, "is_requirement": False, "reasoning": "шум"}]}

    original = _patch(requirement_llm_filter, "call_llm_json", fake_call)
    try:
        verdicts = classify_general_requirements(reqs, _config())
    finally:
        _patch(requirement_llm_filter, "call_llm_json", original)

    assert verdicts[0].is_requirement is False
    assert verdicts[1].is_requirement is True
    assert "не дала вердикт" in verdicts[1].reasoning
    print("OK: пропущенный моделью номер тоже не теряет кандидата молча")


def test_classify_handles_string_typed_booleans_from_provider():
    """Реальный найденный баг (Г.74): GigaChat не даёт строгой JSON-схемы
    (Г.39 — response_format:{"type":"json_object"} отклоняется, JSON
    запрашивается текстом промпта), и на реальном прогоне (347 кандидатов,
    4 раздела) НИ ОДИН не был отсеян — подозрение пало на промпт/модель,
    но фактическая причина была в разборе ответа: `bool("false")` в Python
    равно `True` (любая непустая строка истинна), поэтому если модель
    вернула булево строкой `"false"` вместо литерала `false`, отрицательный
    вердикт молча превращался в положительный ДО того, как дойти до
    render — снаружи неотличимо от «модель ничего не фильтрует»."""
    reqs = [req(1, "Экраны должны быть негорючими."),
            req(2, "Обоснование рациональности трассировки... нет необходимости.")]

    def fake_call(config, system_prompt, user_text, timeout=90.0):
        return {"verdicts": [
            {"index": 1, "is_requirement": "true", "reasoning": "требование"},
            {"index": 2, "is_requirement": "false", "reasoning": "декларация без факта"},
        ]}

    original = _patch(requirement_llm_filter, "call_llm_json", fake_call)
    try:
        verdicts = classify_general_requirements(reqs, _config())
    finally:
        _patch(requirement_llm_filter, "call_llm_json", original)

    assert verdicts[0].is_requirement is True
    assert verdicts[1].is_requirement is False, (
        "строковое \"false\" от провайдера должно давать False, а не "
        "bool(\"false\")==True — иначе фильтр никогда ничего не отсеивает"
    )
    print("OK: строковые true/false от провайдера без строгой JSON-схемы разбираются верно")


def test_render_filtered_summary_shows_counts_and_visible_noise_section():
    verdicts = [
        RequirementVerdict(requirement=req(9, "Экраны должны быть негорючими."),
                           is_requirement=True, reasoning="ok"),
        RequirementVerdict(requirement=req(21, "Работы выполнять в соответствии с ПУЭ."),
                           is_requirement=False, reasoning="голая ссылка на норму"),
    ]
    text = render_general_requirements_summary_llm_filtered(verdicts)
    assert "кандидатов: 2, оставлено: 1, отсеяно как шум: 1" in text
    assert "Экраны должны быть негорючими" in text
    assert "отсеяно как шум (1)" in text
    assert "голая ссылка на норму" in text
    print("OK: отфильтрованная сводка показывает счётчики и видимый список отсеянного, не молчит")


def test_render_filtered_summary_omits_noise_section_when_nothing_dropped():
    verdicts = [RequirementVerdict(requirement=req(1, "Экраны должны быть негорючими."),
                                   is_requirement=True, reasoning="ok")]
    text = render_general_requirements_summary_llm_filtered(verdicts)
    assert "отсеяно как шум: 0" in text  # честный счётчик в заголовке остаётся
    assert "для проверки инспектором" not in text  # но сам раздел не печатается, если отсеивать нечего
    print("OK: пустой список отсеянного не печатает лишний раздел")


if __name__ == "__main__":
    test_system_prompt_does_not_require_obligation_wording()
    test_chunk_requirements_respects_batch_size()
    test_classify_marks_requirement_and_noise_by_index()
    test_classify_across_two_batches_preserves_order()
    test_batch_failure_keeps_candidates_as_requirements_not_silently_dropped()
    test_classify_handles_missing_index_in_response_the_same_way()
    test_classify_handles_string_typed_booleans_from_provider()
    test_render_filtered_summary_shows_counts_and_visible_noise_section()
    test_render_filtered_summary_omits_noise_section_when_nothing_dropped()
    print("ALL PASS")
