"""Регрессии подбора: только синтетические страницы и подменённое зрение."""
from app.compliance import STATUS_NOT_CHECKED, check_compliance
from app.requirement_registry import Requirement
from app.vision_page_compare import _candidate_pages


def test_skipped_page_remains_available_after_file_diversification():
    index = {"101": [{"path": "a.pdf", "page": 1},
                     {"path": "a.pdf", "page": 2},
                     {"path": "b.pdf", "page": 1}]}
    pages = _candidate_pages(["101"], index, 3)
    assert [(p["path"], p["page"]) for p in pages] == [
        ("a.pdf", 1), ("b.pdf", 1), ("a.pdf", 2)]
    print("OK: разнообразие файлов не теряет пропущенную страницу")


def _req(sentence="Требуется альфа", rooms=None):
    return Requirement(rooms=rooms or ["101"], page=1, sentence=sentence)


def _run(reqs, index, vision, **kwargs):
    return check_compliance(reqs, [], [], object(), llm_verify=lambda *a: [],
                            vision_check=vision, room_index=index, **kwargs)


def test_requirement_text_changes_first_candidate_without_discarding_others():
    index = {"101": [{"path": "a.pdf", "page": 1, "text": "бета"},
                     {"path": "a.pdf", "page": 2, "text": "альфа"}]}
    calls = []
    def vision(path, page, sentence, rooms, config):
        calls.append((sentence, page))
        return {"verdict": "confirmed"}
    result = _run([_req(), _req("Требуется бета")], index, vision)
    assert calls == [("Требуется альфа", 2), ("Требуется бета", 1)]
    assert all(len(i.pages_to_check) == 2 for i in result.items)
    assert result.diagnostics["vision_calls"] == 2
    assert result.diagnostics["candidates_available"] == 4
    print("OK: текст требования ранжирует полный набор кандидатов")


def test_direct_room_match_precedes_relevant_level_fallback():
    index = {"101": [{"path": "a.pdf", "page": 1, "text": "бета"},
                     {"path": "b.pdf", "page": 1, "text": "альфа", "level_fallback": True}]}
    result = _run([_req()], index, lambda *a: {"verdict": "unclear"}, max_visual_pages=1)
    assert result.items[0].pages_to_check == [("a.pdf", 1)]
    print("OK: резерв по этажу не вытесняет прямое совпадение")


def test_duplicate_rooms_and_pages_do_not_consume_budget():
    index = {"101": [{"path": "a.pdf", "page": 1}] * 3,
             "102": [{"path": "b.pdf", "page": 1}]}
    result = _run([_req(rooms=["101", "101", "102"])], index,
                  lambda *a: {"verdict": "unclear"}, max_visual_pages=2)
    assert result.items[0].pages_to_check == [("a.pdf", 1), ("b.pdf", 1)]
    assert result.diagnostics["vision_calls"] == 2
    print("OK: дубли не расходуют бюджет")


def test_failed_vision_is_not_reported_as_completed_inspection():
    def vision(*args):
        raise ConnectionError("synthetic failure")
    result = check_compliance([_req()], [], [], object(), vision_check=vision,
                             candidate_pages=lambda *a: [("a.pdf", 1)])
    assert result.items[0].status == STATUS_NOT_CHECKED
    assert "не выполнялся" in result.items[0].detail
    assert result.diagnostics["vision_errors"] == 1
    print("OK: сбой зрения не выдаётся за просмотр")


def test_unclear_and_error_are_counted_separately():
    index = {"101": [{"path": "a.pdf", "page": 1}, {"path": "b.pdf", "page": 1}]}
    result = _run([_req()], index, lambda path, *a: (
        {"verdict": "unclear", "error": True} if path == "a.pdf"
        else {"verdict": "unclear", "reason": "не видно"}))
    assert result.diagnostics["vision_errors"] == 1
    assert result.diagnostics["vision_unclear"] == 1
    assert "частично" in result.items[0].detail
    print("OK: частичный просмотр отличается от полного сбоя")


def test_full_budget_preserves_every_page_and_input():
    import copy
    index = {"101": [{"path": "a.pdf", "page": n, "text": "альфа" if n % 2 else ""}
                     for n in range(1, 8)],
             "102": [{"path": "b.pdf", "page": 1, "level_fallback": True}]}
    original = copy.deepcopy(index)
    result = _run([_req(rooms=["101", "102"])], index,
                  lambda *a: {"verdict": "unclear"}, max_visual_pages=20)
    assert set(result.items[0].pages_to_check) == {
        (e["path"], e["page"]) for group in index.values() for e in group}
    assert index == original
    print("OK: полный бюджет сохраняет все страницы и исходный индекс")


def test_http_room_index_preserves_text_and_fallback_provenance(monkeypatch):
    from types import SimpleNamespace

    from app import main
    monkeypatch.setattr(main.facts_store, "facts_for", lambda *a: SimpleNamespace(
        room_facts=[{"key": "101", "page": 1}],
        text_facts=[{"page": 1, "text": "альфа"}, {"page": 2, "text": "бета"}]))
    def augment(index, paths):
        index["101"].append({"path": paths[0], "page": 2, "level_fallback": True})
        return index
    monkeypatch.setattr(main, "augment_room_index_with_level_fallback", augment)
    index = main._rd_room_index([("a.pdf", "synthetic")])
    assert [e["text"] for e in index["101"]] == ["альфа", "бета"]
    assert index["101"][1]["level_fallback"] is True
    print("OK: HTTP-индекс передаёт текст и происхождение резерва")


def test_unreadable_provider_response_is_explicit_error(monkeypatch):
    from app import vision_page_compare as vision
    monkeypatch.setattr(vision, "render_page_to_data_url", lambda *a: "synthetic")
    for answer in (None, [], {"verdict": "unexpected"}):
        monkeypatch.setattr(vision, "call_llm_json", lambda *a, answer=answer, **kw: answer)
        result = vision.check_requirement_on_page("a.pdf", 1, "альфа", [], None)
        assert result["error"] is True
        assert result["verdict"] == "unclear"
    print("OK: неразбираемый ответ помечен как ошибка")
