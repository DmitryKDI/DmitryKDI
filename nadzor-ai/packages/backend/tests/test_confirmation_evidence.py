"""Проверяем происхождение подтверждений на вымышленных фрагментах."""
import pytest

from app import requirement_text_verify as verifier
from app.compliance import check_compliance, STATUS_CONFIRMED, STATUS_NOT_CHECKED
from app.requirement_registry import Requirement


def run(monkeypatch, item, facts=None, requirements=None):
    facts = facts or [{"page": 2, "document": "условный том", "text": "Элемент установлен."}]
    requirements = requirements or [Requirement(rooms=[], page=1, sentence="Установить элемент.")]
    monkeypatch.setattr(verifier, "call_llm_json", lambda *a, **kw: {"verdicts": [item]})
    return check_compliance(requirements, facts, [], object(),
                            llm_verify=verifier.verify_general_requirements_llm)


def confirmation(**changes):
    item = {"id": "R1", "verdict": "confirmed", "coverage": "full",
            "reason": "Условие выполнено.",
            "evidence": [{"fact_id": 1, "page": 2, "quote": "Элемент установлен."}]}
    item.update(changes)
    return item


@pytest.mark.parametrize("change", [
    {"coverage": "partial"}, {"evidence": []},
    {"evidence": [{"fact_id": 1, "page": 3, "quote": "Элемент установлен."}]},
    {"evidence": [{"fact_id": 1, "page": 2, "quote": "Другое утверждение."}]},
    {"evidence": [{"fact_id": 2, "page": 2, "quote": "Элемент установлен."}]},
])
def test_partial_or_untraceable_evidence_cannot_confirm(monkeypatch, change):
    result = run(monkeypatch, confirmation(**change))
    assert result.items[0].status != STATUS_CONFIRMED
    print("OK: неполное или неподтверждаемое свидетельство не подтверждает требование")


def test_valid_quote_is_visible_and_does_not_transfer_to_duplicate(monkeypatch):
    reqs = [Requirement(rooms=[], page=p, sentence="Установить элемент.") for p in (1, 3)]
    result = run(monkeypatch, confirmation(), requirements=reqs)
    by_page = {i.requirement.page: i for i in result.items}
    assert by_page[1].status == STATUS_CONFIRMED
    assert by_page[3].status != STATUS_CONFIRMED
    assert "условный том" in by_page[1].detail
    assert "Элемент установлен." in by_page[1].detail
    print("OK: цитата видна, вердикт не переносится на повтор формулировки")


def test_all_failed_calls_are_not_checked(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("недоступен")
    monkeypatch.setattr(verifier, "call_llm_json", fail)
    result = check_compliance(
        [Requirement(rooms=[], page=1, sentence="Установить элемент.")],
        [{"page": 2, "text": "Элемент установлен."}], [], object(),
        llm_verify=verifier.verify_general_requirements_llm)
    assert result.items[0].status == STATUS_NOT_CHECKED
    assert result.not_run
    print("OK: полный сбой не выдан за выполненную проверку")


def test_conflicting_text_evidence_requires_manual_check(monkeypatch):
    facts = [
        {"page": 2, "document": "том А", "text": "Элемент установлен."},
        {"page": 5, "document": "том Б", "text": "Установка элемента отменена."},
    ]
    calls = []

    def answer(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"verdicts": [confirmation()]}
        return {"verdicts": [{
            "id": "R1", "verdict": "absent", "coverage": "full",
            "reason": "решение отменено", "evidence": [
                {"fact_id": 2, "page": 5, "quote": "Установка элемента отменена."}],
        }]}

    monkeypatch.setattr(verifier, "call_llm_json", answer)
    result = check_compliance(
        [Requirement(rooms=[], page=1, sentence="Установить элемент.")],
        facts, [], object(),
        llm_verify=lambda reqs, text, cfg: verifier.verify_general_requirements_llm(
            reqs, text, cfg, max_chars_per_call=20),
    )

    assert result.items[0].status != STATUS_CONFIRMED
    assert "противореч" in result.items[0].detail.lower()
    assert "том А" in result.items[0].detail and "том Б" in result.items[0].detail
    print("OK: противоречивые цитаты не превращаются в подтверждение")
