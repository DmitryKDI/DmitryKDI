"""Серверный сценарий «инспектор загрузил ПД, нажал кнопку» (Г.94).

До этого стадия разбора ПД (Г.86) существовала ТОЛЬКО в CLI: сервер умел
запускать сравнение ПД↔РД, а самостоятельный разбор ПД — нет. Инспектор
через интерфейс не мог получить сводку вообще. Здесь проверяется контракт
серверного прогона: связь проверяется ДО работы, отказ виден явно, а не
выдаётся за пустой результат (Г.10/Г.77/Г.91).
"""
import os
import sys
import time
from pathlib import Path

TEST_DB = "/tmp/nadzor_pd_run_test.db"  # noqa: S108 — временная БД теста, как в test_api_integration
Path(TEST_DB).unlink(missing_ok=True)
os.environ["NADZOR_DB_PATH"] = TEST_DB

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402
from app import main as main_module  # noqa: E402
from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.requirement_registry import Requirement  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

init_db()
client = TestClient(app)


def _upload(tmp_path: Path, text: str = "Экраны должны быть негорючими.") -> int:
    pdf = tmp_path / "pd.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(str(pdf))
    doc.close()
    with pdf.open("rb") as f:
        r = client.post("/documents?side=before", files={"file": ("pd.pdf", f, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _set_provider(provider: str, api_key: str = "") -> None:
    """Тесты в одном процессе делят базу, поэтому нужное состояние настроек
    задаётся явно, а не наследуется от порядка запуска."""
    client.put("/settings", json={"provider": provider, "base_url": "",
                                  "model": "", "api_key": api_key})


def _wait(run_id: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/pd-runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("прогон не завершился за отведённое время")


def test_one_button_run_produces_summary(tmp_path, monkeypatch):
    """Главный сценарий: загруженный документ + один POST = готовая сводка.
    Промпт зашит в код, инспектор его не видит и не вводит."""
    doc_id = _upload(tmp_path)
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    monkeypatch.setattr(
        main_module, "extract_requirements_llm",
        lambda facts, config, **kw: [Requirement(
            rooms=[], page=1, sentence="Экраны должны быть негорючими.", code=None,
            document="pd.pdf", section="ОВ", summary="Экраны негорючие")],
    )
    r = client.post("/pd-runs", json={"document_ids": [doc_id]})
    assert r.status_code == 200, r.text
    body = _wait(r.json()["id"])

    assert body["status"] == "done", body
    assert body["requirements_total"] == 1
    assert "Экраны негорючие" in body["summary"], "в сводке короткая суть (Г.93)"
    assert body["store_run_id"], "прогон сохранён в хранилище разборов ПД (Г.87)"


def test_no_connection_stops_the_run_instead_of_returning_empty(tmp_path, monkeypatch):
    """Г.77/Г.91 — оборванная связь не должна выглядеть как «в документе
    ничего нет». Это самая дорогая ошибка проекта: она уже стоила трёх
    раундов правок промпта против бага, которого в промпте не было."""
    doc_id = _upload(tmp_path)
    monkeypatch.setattr(main_module, "check_llm_reachable",
                        lambda cfg: (False, "ConnectError: сеть недоступна"))
    called = []
    monkeypatch.setattr(main_module, "extract_requirements_llm",
                        lambda *a, **kw: called.append(1) or [])

    body = _wait(client.post("/pd-runs", json={"document_ids": [doc_id]}).json()["id"])

    assert body["status"] == "error"
    assert "сеть недоступна" in (body["error"] or ""), "причина названа целиком"
    assert not called, "разбор не начинался — связь проверяется ДО работы, а не по её итогу"
    assert not body["summary"], "пустая сводка не выдаётся за результат"


def test_missing_document_is_named_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    body = _wait(client.post("/pd-runs", json={"document_ids": [999999]}).json()["id"])
    assert body["status"] == "error"
    assert "999999" in (body["error"] or "")


def test_llm_check_endpoint_reports_state_for_the_button(monkeypatch):
    """Кнопка «Проверить связь» в интерфейсе: инспектор узнаёт о проблеме
    до того, как запустил разбор на сотни страниц."""
    _set_provider("gigachat", api_key="")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "тестовый-ключ")
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "связь есть"))
    ok = client.get("/llm-check").json()
    assert ok["reachable"] is True and ok["provider"] == "gigachat"

    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (False, "401 Unauthorized"))
    bad = client.get("/llm-check").json()
    assert bad["reachable"] is False and "401" in bad["message"]


def test_llm_check_says_no_key_instead_of_pretending_to_check(monkeypatch):
    """«Ключа нет» и «связь не прошла» — разные состояния с разными
    действиями администратора; смешивать их значит повторять подмену,
    которую запрещает Г.10."""
    _set_provider("gigachat", api_key="")
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    called = []
    monkeypatch.setattr(main_module, "check_llm_reachable",
                        lambda cfg: called.append(1) or (True, "не должно вызваться"))
    body = client.get("/llm-check").json()
    assert body["reachable"] is False
    assert "ключ" in body["message"] and "GIGACHAT_CREDENTIALS" in body["message"]
    assert not called, "без ключа проверять нечего — вызов не делается"


def test_gigachat_is_the_default_provider():
    """Инструмент делается под GigaChat: провайдер по умолчанию — он, а не
    тот, что оставался от разработки.

    Проверяется УМОЛЧАНИЕ схемы, а не текущая строка настроек: другие тесты
    в том же процессе законно меняют провайдера под свои моки, и проверка
    живого состояния была бы проверкой порядка запуска тестов, а не кода.
    """
    from app import models
    assert models.Settings.__table__.c.provider.default.arg == "gigachat"


def test_rd_run_reports_composition_even_without_requirements(tmp_path, monkeypatch):
    """Г.95 — разбор РД: если связного текста нет, инспектор всё равно должен
    увидеть, ИЗ ЧЕГО состоит том. Пустая сводка без состава неотличима от
    сбоя, а у рабочей документации текста нет по природе (Г.8)."""
    doc_id = _upload(tmp_path, text="")
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    monkeypatch.setattr(main_module, "extract_requirements_llm", lambda facts, config, **kw: [])

    body = _wait(client.post("/pd-runs", json={"document_ids": [doc_id], "side": "after"}).json()["id"])

    assert body["status"] == "done"
    assert body["side"] == "after"
    assert "листов всего" in body["composition"], body["composition"]
    assert body["requirements_total"] == 0
    assert body["store_run_id"] is None, "разбор РД в хранилище разборов ПД не попадает"


def test_pd_run_still_saves_to_store_and_gets_composition_too(tmp_path, monkeypatch):
    """Состав считается для обеих сторон, а в хранилище идёт только ПД."""
    doc_id = _upload(tmp_path)
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    monkeypatch.setattr(
        main_module, "extract_requirements_llm",
        lambda facts, config, **kw: [Requirement(
            rooms=[], page=1, sentence="Экраны негорючие.", code=None,
            document="pd.pdf", section="ОВ", summary="Экраны негорючие")],
    )
    body = _wait(client.post("/pd-runs", json={"document_ids": [doc_id], "side": "before"}).json()["id"])

    assert body["status"] == "done" and body["side"] == "before"
    assert "листов всего" in body["composition"]
    assert body["store_run_id"], "разбор ПД сохранён для стадии сверки"


def test_unknown_side_is_rejected_explicitly(tmp_path):
    doc_id = _upload(tmp_path)
    r = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "сбоку"})
    assert r.status_code == 400 and "side" in r.json()["detail"]
