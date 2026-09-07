"""Тесты хранилища результатов разбора ПД (Г.87).

Каждый тест работает на своей временной базе (`NADZOR_PD_STORE`), чтобы
прогон тестов не писал в накопленный датасет пользователя и чтобы тесты не
зависели друг от друга через общий файл.
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.requirement_registry import Requirement  # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Свежий модуль хранилища на временном файле базы."""
    monkeypatch.setenv("NADZOR_PD_STORE", str(tmp_path / "pd_store.db"))
    from app import pd_store
    importlib.reload(pd_store)
    return pd_store


def _req(page, sentence, section="ООС", document="Том ООС8.1.pdf", rooms=None, code=None):
    return Requirement(rooms=rooms or [], page=page, sentence=sentence,
                       code=code, document=document, section=section)


def test_saved_requirements_come_back_unchanged(store):
    """Стадия 2 читает то же, что стадия 1 записала — иначе сверка пойдёт
    по искажённым данным, и расхождение будет не в документах, а в нас."""
    reqs = [
        _req(12, "Отходы вывозить по договору."),
        _req(3, "В пом. 140 предусмотреть вытяжку.", section="ОВ",
             document="Том ОВ.pdf", rooms=["140"], code="П6"),
    ]
    run_id = store.save_run(reqs, documents=["Том ООС8.1.pdf", "Том ОВ.pdf"], extractor="llm")
    loaded = sorted(store.load_run(run_id), key=lambda r: r.page)

    assert len(loaded) == 2
    assert loaded[0].page == 3 and loaded[0].rooms == ["140"] and loaded[0].code == "П6"
    assert loaded[0].section == "ОВ" and loaded[0].document == "Том ОВ.pdf"
    assert loaded[1].sentence == "Отходы вывозить по договору."
    print("OK: сохранённые требования читаются без потерь")


def test_latest_run_found_by_document(store):
    """Стадия 2 должна находить последний разбор ИМЕННО этого тома, а не
    любой последний: в работе по одному тому за раз прогоны идут подряд."""
    store.save_run([_req(1, "первый", document="А.pdf")], documents=["А.pdf"], extractor="llm")
    second = store.save_run([_req(1, "второй", document="Б.pdf")],
                            documents=["Б.pdf"], extractor="llm")
    third = store.save_run([_req(2, "третий", document="А.pdf")],
                           documents=["А.pdf"], extractor="llm")

    assert store.latest_run_id("Б.pdf") == second
    assert store.latest_run_id("А.pdf") == third
    assert store.latest_run_id() == third
    print("OK: последний прогон находится по имени документа")


def test_missing_run_returns_none_not_empty_success(store):
    """Г.10 — «разбора не было» и «разбор дал ноль требований» не должны
    выглядеть одинаково: первое обязано быть отличимо вызывающим кодом."""
    assert store.latest_run_id("никогда-не-разбирался.pdf") is None
    assert store.load_run(999) == []
    print("OK: отсутствие прогона отличимо от пустого результата")


def test_run_records_extractor_and_prompt_version(store):
    """Без этих полей датасет смешивает результат модели с результатом
    регулярки и разные версии промпта — фильтровать его будет нечем."""
    store.save_run([_req(1, "x")], documents=["А.pdf"], extractor="regex")
    store.save_run([_req(1, "y")], documents=["А.pdf"], extractor="llm",
                   provider="gigachat", model="GigaChat-2", failed_chunks=3)
    runs = store.list_runs()

    assert runs[0]["extractor"] == "llm" and runs[0]["failed_chunks"] == 3
    assert runs[1]["extractor"] == "regex"
    assert all(r["requirements_total"] == 1 for r in runs)
    print("OK: прогон помнит извлекатель, провайдера, модель и число сбоев")


def test_export_dataset_writes_jsonl_with_context(store, tmp_path):
    """Датасет на будущее бесполезен без контекста получения: по нему
    нельзя будет отделить надёжные строки от сомнительных."""
    store.save_run([_req(7, "Шумозащитные экраны предусмотрены.")],
                   documents=["Том ООС8.1.pdf"], extractor="llm",
                   provider="gigachat", model="GigaChat-2")
    out = tmp_path / "dataset.jsonl"
    count = store.export_dataset(str(out))

    assert count == 1
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["sentence"] == "Шумозащитные экраны предусмотрены."
    assert row["section"] == "ООС" and row["page"] == 7
    assert row["extractor"] == "llm" and row["provider"] == "gigachat"
    assert row["prompt_version"] == store.PROMPT_VERSION
    print("OK: выгрузка датасета несёт текст и условия его получения")


def test_store_is_append_only_across_runs(store):
    """Повторный разбор того же тома НЕ затирает прошлый: датасет копится,
    а сравнить две версии разбора — законный сценарий."""
    store.save_run([_req(1, "версия 1")], documents=["А.pdf"], extractor="regex")
    store.save_run([_req(1, "версия 2")], documents=["А.pdf"], extractor="llm")
    assert len(store.list_runs()) == 2
    print("OK: прогоны копятся, повторный разбор не затирает прошлый")


def test_run_without_requirements_is_still_findable(store):
    """Реальный изъян, пойманный собственным тестом при написании: искали
    прогон по строкам требований, и том, в котором честно не нашлось ни
    одного требования, выглядел бы как «никогда не разбирался». Это подмена
    «пусто» на «не делали» — ровно то, что запрещает Г.10."""
    run_id = store.save_run([], documents=["Пустой том.pdf"], extractor="llm")
    assert store.latest_run_id("Пустой том.pdf") == run_id
    assert store.load_run(run_id) == []
    print("OK: прогон без единого требования всё равно находится по документу")
