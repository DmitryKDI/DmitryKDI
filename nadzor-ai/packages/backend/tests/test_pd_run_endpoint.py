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
from unittest.mock import Mock

import pytest

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


@pytest.mark.parametrize("partial", [False, True])
def test_failed_extraction_chunks_are_not_saved_as_a_successful_run(tmp_path, monkeypatch, partial):
    doc_id = _upload(tmp_path, text="Document requirements")
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    saved = Mock(return_value=101)
    monkeypatch.setattr(main_module, "save_run", saved)
    monkeypatch.setattr(main_module, "attach_rooms_by_name", lambda *args: None)
    monkeypatch.setattr(main_module, "attach_norms", lambda *args: ([], ""))
    monkeypatch.setattr(main_module, "record_profile", lambda *args: None)
    monkeypatch.setattr(main_module, "render_section_knowledge", lambda *args: "")

    def extract(facts, config, **callbacks):
        callbacks["on_chunk_error"](1, RuntimeError("служебные подробности"))
        return [Requirement(rooms=[], page=1, sentence="Требование", code=None,
                            document="pd.pdf", section=None)] if partial else []

    monkeypatch.setattr(main_module, "extract_requirements_llm", extract)
    body = _wait(client.post("/pd-runs", json={"document_ids": [doc_id]}).json()["id"])
    assert body["status"] == "error", body
    assert body["requirements_total"] == int(partial)
    assert "ошибок пачек 1" in body["error"]
    assert "неполное" in body["summary"]
    assert "служебные подробности" not in body["error"]
    assert saved.call_args.kwargs["failed_chunks"] == 1
    print("OK: полный и частичный сбои извлечения явно ошибочны, найденное сохранено")


def test_incomplete_saved_extraction_cannot_start_comparison(monkeypatch):
    from app import models
    from app.db import get_session

    with next(get_session()) as db:
        source = models.PdRun(status="error", store_run_id=101, document_ids=[])
        db.add(source)
        db.flush()
        run = models.ComplianceRun(pd_run_id=source.id, rd_document_ids=[])
        db.add(run)
        db.commit()
        run_id = run.id
    loaded = Mock(side_effect=AssertionError("Неполный разбор не читать для сверки"))
    monkeypatch.setattr(main_module, "load_run", loaded)
    main_module._run_compliance(run_id)
    with next(get_session()) as db:
        result = db.get(models.ComplianceRun, run_id)
        assert result.status == "error"
        assert "не запускалась" in result.error
    loaded.assert_not_called()
    print("OK: сверка не выдаёт неполный разбор за полную исходную документацию")


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

    started = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "after"})
    body = _wait(started.json()["id"])

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
    started = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "before"})
    body = _wait(started.json()["id"])

    assert body["status"] == "done" and body["side"] == "before"
    assert "листов всего" in body["composition"]
    assert body["store_run_id"], "разбор ПД сохранён для стадии сверки"


def test_unknown_side_is_rejected_explicitly(tmp_path):
    doc_id = _upload(tmp_path)
    r = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "сбоку"})
    assert r.status_code == 400 and "side" in r.json()["detail"]


def _done_pd_run(tmp_path, monkeypatch) -> int:
    doc_id = _upload(tmp_path)
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))
    monkeypatch.setattr(
        main_module, "extract_requirements_llm",
        lambda facts, config, **kw: [Requirement(
            rooms=[], page=1, sentence="Регистры по ГОСТ 8732-78.", code=None,
            document="pd.pdf", section="ОВ", summary="Регистры ГОСТ 8732-78")],
    )
    started = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "before"})
    return _wait(started.json()["id"])["id"]


def _wait_compliance(run_id: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/compliance-runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError("сверка не завершилась за отведённое время")


def test_compliance_uses_saved_pd_run_and_never_says_violation(tmp_path, monkeypatch):
    """Г.96 — третья кнопка: требования берутся из сохранённого разбора ПД,
    а не извлекаются заново. Отчёт не называет ничего нарушением."""
    _set_provider("gigachat", api_key="")
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    pd_run_id = _done_pd_run(tmp_path, monkeypatch)
    rd_id = _upload(tmp_path, text="Трубы стальные бесшовные ГОСТ 8732-78 по спецификации.")

    body = _wait_compliance(client.post(
        "/compliance-runs", json={"pd_run_id": pd_run_id, "rd_document_ids": [rd_id]},
    ).json()["id"])

    assert body["status"] == "done", body
    assert body["requirements_total"] == 1
    assert "не является заключением" in body["report"].lower()
    assert body["counts"], body


def test_compliance_without_pd_run_refuses_explicitly(tmp_path):
    rd_id = _upload(tmp_path)
    body = _wait_compliance(client.post(
        "/compliance-runs", json={"pd_run_id": 999999, "rd_document_ids": [rd_id]},
    ).json()["id"])
    assert body["status"] == "error"
    assert "разбор ПД" in (body["error"] or "")


def test_section_can_be_set_by_hand_when_file_is_not_recognised(tmp_path, monkeypatch):
    """Г.97 — автоматическое определение раздела остаётся, но у инспектора
    есть последнее слово. Файл без узнаваемых признаков (имя, титул, штамп)
    раньше уходил в разбор с «раздел не определён», и требования оставались
    без привязки — а привязка к разделу это прямое требование пользователя
    (Г.86)."""
    doc_id = _upload(tmp_path, text="Ничего узнаваемого.")
    before = client.get("/documents").json()
    mine = [d for d in before if d["id"] == doc_id][0]
    assert mine["discipline_code"] is None, "файл действительно не опознан автоматически"

    r = client.patch(f"/documents/{doc_id}", json={"discipline_code": "ВК"})
    assert r.status_code == 200, r.text
    assert r.json()["discipline_code"] == "ВК"
    assert r.json()["classification_source"] == "manual", "источник виден: раздел задан руками"


def test_manual_section_reaches_the_run_and_wins_over_guessing(tmp_path, monkeypatch):
    """Ручной выбор должен доезжать до разбора, а не оставаться меткой в
    списке файлов: иначе «поправил» и ничего не изменилось."""
    doc_id = _upload(tmp_path, text="Ничего узнаваемого.")
    client.patch(f"/documents/{doc_id}", json={"discipline_code": "ЭОМ"})

    seen: list = []
    monkeypatch.setattr(main_module, "check_llm_reachable", lambda cfg: (True, "мок"))

    def fake_extract(facts, config, **kw):
        seen.extend({f.get("section") for f in facts})
        return []

    monkeypatch.setattr(main_module, "extract_requirements_llm", fake_extract)
    started = client.post("/pd-runs", json={"document_ids": [doc_id], "side": "before"})
    body = _wait(started.json()["id"])

    assert body["status"] == "done"
    assert seen == ["ЭОМ"], f"раздел из ручной правки не доехал до разбора: {seen}"


def test_unknown_section_code_is_rejected_with_the_list(tmp_path):
    """Опечатка в коде раздела не должна тихо сохраниться: ошибка называет
    допустимые значения, иначе инспектор не поймёт, что ввёл не то."""
    doc_id = _upload(tmp_path)
    r = client.patch(f"/documents/{doc_id}", json={"discipline_code": "ЫЫЫ"})
    assert r.status_code == 400
    assert "ОВ" in r.json()["detail"], r.json()


def test_manual_section_can_be_cleared_back_to_automatic(tmp_path):
    """Ручную правку можно отменить — вернуться к тому, что определила
    программа, а не остаться навсегда с ошибочным вводом."""
    doc_id = _upload(tmp_path)
    client.patch(f"/documents/{doc_id}", json={"discipline_code": "КР"})
    r = client.patch(f"/documents/{doc_id}", json={"discipline_code": None})
    assert r.status_code == 200
    assert r.json()["classification_source"] != "manual"


def test_level_fallback_is_wired_into_the_room_index(tmp_path, monkeypatch):
    """Г.98 — резерв по отметке этажа (Г.40) подключён к сверке: план и его
    экспликация лежат на разных листах, и без резерва требование, чьё
    помещение не нашлось в текстовом слое РД, оставалось бы без единого
    кандидата на просмотр."""
    called = []
    monkeypatch.setattr(main_module, "augment_room_index_with_level_fallback",
                        lambda index, paths: called.append(paths) or index)
    doc_id = _upload(tmp_path)
    assert any(d["id"] == doc_id for d in client.get("/documents").json())

    index = main_module._rd_room_index([("путь.pdf", "рд.pdf", None)])

    assert isinstance(index, dict)
    assert called, "резерв по отметке этажа вызывается при построении реестра"


def test_composition_is_produced_even_when_llm_is_unavailable(tmp_path, monkeypatch):
    """Г.98 — найдено прогоном «как инспектор» на реальном комплекте: без
    ключа обе кнопки возвращали только ошибку, хотя состав комплекта —
    детерминированная работа, модели не требующая. Теперь состав считается
    ДО проверки связи и отдаётся вместе с причиной, по которой требования не
    извлекались."""
    doc_id = _upload(tmp_path)
    monkeypatch.setattr(main_module, "check_llm_reachable",
                        lambda cfg: (False, "ключ ЛЛМ не задан — проверять нечего"))

    body = _wait(client.post("/pd-runs", json={"document_ids": [doc_id]}).json()["id"])

    assert body["status"] == "error", "прогон не выдаёт себя за успешный"
    assert "листов всего" in body["composition"], "состав всё равно посчитан и отдан"
    assert "требования НЕ извлекались" in (body["error"] or "")
    assert body["requirements_total"] == 0
