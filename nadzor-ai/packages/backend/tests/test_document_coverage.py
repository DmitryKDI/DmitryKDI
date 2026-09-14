"""Покрытие определяется свидетельствами обработки, а не наличием находок."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.document_coverage import build_coverage
from app.documents import DocumentFacts


def _document(**changes):
    values = {
        "id": 1, "name": "document.pdf", "side": "after", "pages": 3,
        "discipline_code": None, "status": "ok", "digest": "test-digest",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _facts(**changes):
    values = {
        "name": "document.pdf", "pages": 3,
        "text_facts": [{"page": 1, "text": "Текст"}], "room_facts": [],
        "page_kinds": {1: "text", 2: "drawing"},
    }
    values.update(changes)
    return DocumentFacts(**values)


def test_empty_selection_is_not_successful_processing():
    report = build_coverage([], {}, Mock(side_effect=AssertionError("не читать")))
    assert not report.ingestion_complete
    assert not report.text_extraction_complete
    assert report.comparison_status == "not_assessed"
    print("OK: пустой набор не назван полностью обработанным")


def test_missing_ids_survive_and_duplicates_do_not_inflate_coverage():
    report = build_coverage([1, 2, 1], {1: _document()}, lambda _: _facts())
    assert report.documents_requested == 2
    assert report.documents_present == report.documents_missing == 1
    assert report.documents[1].document_id == 2
    assert report.documents[1].status == "missing"
    assert report.documents_unknown_page_count == 1
    assert not report.ingestion_complete
    print("OK: отсутствующий документ виден, повтор ID не увеличивает покрытие")


def test_every_known_page_has_state_and_textless_pages_are_not_clean():
    report = build_coverage([1], {1: _document()}, lambda _: _facts())
    assert report.pages_known == 3
    assert report.pages_processed == 2
    assert report.pages_unprocessed == report.pages_without_text == 1
    assert [p.status for p in report.documents[0].pages] == [
        "processed", "processed", "unprocessed"]
    assert report.documents_partial == 1
    assert report.documents[0].pages[1].reason
    assert not report.text_extraction_complete
    print("OK: страницы без текста и страницы без разбора имеют разные состояния")


def test_exclusion_stays_in_denominator_with_its_reason():
    report = build_coverage([1], {1: _document()}, lambda _: _facts(excluded={3: "Приложение"}))
    assert report.pages_known == report.pages_processed + report.pages_excluded == 3
    assert report.documents[0].pages[-1].reason == "Приложение"
    assert not report.ingestion_complete
    print("OK: исключённая страница не исчезает из покрытия")


@pytest.mark.parametrize("status", ["ok", "parsing", "error", "unknown"])
def test_document_status_without_saved_facts_cannot_prove_page_processing(status):
    report = build_coverage([1], {1: _document(status=status)}, lambda _: None)
    assert report.pages_unprocessed == 3
    assert report.pages_processed == 0
    assert not report.ingestion_complete
    assert report.documents[0].status == ("error" if status == "error" else "unprocessed")
    print("OK: состояние документа само по себе не доказывает обработку страниц")


def test_cache_failure_does_not_hide_other_documents_or_leak_exception_details():
    reader = Mock(side_effect=[RuntimeError("секретные подробности"), _facts()])
    report = build_coverage([1, 2], {1: _document(), 2: _document(id=2)}, reader)
    assert report.documents_error == report.documents_partial == 1
    assert report.pages_known == 6
    assert "секретные подробности" not in report.model_dump_json()
    print("OK: сбой кэша назван ошибкой, остальные документы остаются в отчёте")


def test_document_failure_does_not_invent_the_page_where_it_happened():
    report = build_coverage([1], {1: _document(status="error")}, lambda _: _facts())
    assert report.documents_error == 1
    assert report.pages_processed == 2
    assert report.documents[0].pages[-1].status == "unprocessed"
    assert not report.ingestion_complete
    print("OK: сохранённые факты видны, место ошибки не выдумывается")


def test_conflicting_page_count_never_hides_unprocessed_tail():
    report = build_coverage([1], {1: _document(pages=4)}, lambda _: _facts())
    assert report.pages_known == 4
    assert report.pages_unprocessed == 2
    assert report.documents_error == 1
    print("OK: расхождение числа страниц явно ошибочно, хвост не скрыт")


def test_zero_unknown_pages_are_not_a_complete_document():
    report = build_coverage([1], {1: _document(pages=0)}, lambda _: None)
    assert report.documents[0].page_count is None
    assert report.documents_unknown_page_count == 1
    assert not report.ingestion_complete
    print("OK: неизвестное число страниц не выдаётся за пустой обработанный документ")


@pytest.mark.parametrize("side", ["before", "after"])
def test_complete_text_extraction_does_not_claim_package_completeness_or_comparison(side):
    report = build_coverage([1], {1: _document(pages=1, side=side)},
                            lambda _: _facts(pages=1, page_kinds={1: "text"}))
    assert report.ingestion_complete and report.text_extraction_complete
    assert report.package_completeness == report.comparison_status == "not_assessed"
    assert report.documents[0].side == side
    assert report.documents[0].discipline_code is None
    print("OK: общий механизм не угадывает стадию, раздел или результат сверки")


def test_complete_page_parse_with_no_text_requires_a_separate_reading_path():
    report = build_coverage([1], {1: _document(pages=1)},
                            lambda _: _facts(pages=1, page_kinds={1: "drawing"}, text_facts=[]))
    assert report.ingestion_complete
    assert not report.text_extraction_complete
    assert report.pages_without_text == 1
    print("OK: технический разбор растрового листа не приравнивается к его чтению")


def test_coverage_api_reads_saved_facts_without_starting_processing(monkeypatch):
    from app import facts_store, main, models
    from app.db import Base, get_session
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(models.Document(id=1, name="document.pdf", side="after", file_path="unused",
                               pages=3, status="ok", digest="test-digest"))
        db.commit()

    def session():
        with factory() as db:
            yield db

    stored = Mock(return_value=_facts())
    monkeypatch.setattr(facts_store, "stored", stored)
    monkeypatch.setattr(facts_store, "facts_for", Mock(side_effect=AssertionError("не разбирать")))
    main.app.dependency_overrides[get_session] = session
    try:
        response = TestClient(main.app).post("/documents/coverage", json={"document_ids": [1, 2]})
        assert response.status_code == 200, response.text
        assert response.json()["documents_missing"] == 1
        stored.assert_called_once_with("test-digest", touch=False)
        facts_store.facts_for.assert_not_called()
    finally:
        main.app.dependency_overrides.pop(get_session, None)
        engine.dispose()
    print("OK: API покрытия читает кэш, не вызывает разбор или модель")
