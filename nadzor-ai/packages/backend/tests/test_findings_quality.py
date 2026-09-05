"""Находка должна быть рабочим указанием инспектору, а не строкой diff.

Здесь проверяется то, что отличает вывод для надзора от простого списка
различий: степень критичности задаёт порядок обхода объекта, field_check
говорит, что там сделать, а попытка внушить что-либо модели через содержимое
документа сама становится находкой (модель угроз, Б.3.5).
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

import os

# Тот же файл базы, что и у test_api_integration: app.db читает переменную
# один раз при импорте, и разные пути в двух файлах дали бы результат,
# зависящий от порядка импорта.
TEST_DB = "/tmp/nadzor_backend_test.db"
os.environ.setdefault("NADZOR_DB_PATH", TEST_DB)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app import db as db_module
from app.db import Base, init_db
from app.llm import LlmConfig
from app.main import _normalize_severity, app
from app.vision import compare_text_pair

client = TestClient(app)

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_severity_synonyms_are_normalized():
    assert _normalize_severity("критично") == "critical"
    assert _normalize_severity("Критическое") == "critical"
    assert _normalize_severity("critical") == "critical"
    assert _normalize_severity("существенно") == "major"
    assert _normalize_severity("medium") == "major"
    assert _normalize_severity("незначительно") == "minor"
    assert _normalize_severity("low") == "minor"
    print("OK: синонимы степени приводятся к ключам контракта находки")


def test_negated_severity_is_not_read_as_significant():
    """«Незначительно» содержит корень «значи» — без явного порядка проверки
    самая безобидная находка встала бы в начало списка обхода."""
    assert _normalize_severity("незначительное") == "minor"
    assert _normalize_severity("не значимо") == "minor"
    assert _normalize_severity("не критично") == "minor"
    assert _normalize_severity("не-существенно") == "minor"
    print("OK: отрицание не превращается в существенную находку")


def test_unknown_severity_is_left_empty_not_guessed():
    """Пустая степень честнее выдуманной: находка остаётся видимой, но не
    притворяется оценённой."""
    for value in ("", None, "ой не знаю", 42):
        assert _normalize_severity(value) == ""
    print("OK: неразобранная степень не выдаётся за настоящую")


def test_text_compare_wraps_document_in_untrusted_container():
    """Мера Б.3.1: содержимое документа уходит модели внутри явного
    контейнера, отделяющего данные от инструкций."""
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["user"] = json["messages"][0]["content"][0]["text"]
        return _FakeResponse({"content": [{"text": '{"significant": []}'}]})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        compare_text_pair("бетон B30", "бетон B25", LlmConfig(provider="anthropic", api_key="sk-ant-test"))

    assert captured["user"].count("<НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>") == 2
    assert captured["user"].count("</НЕДОВЕРЕННЫЙ_ДОКУМЕНТ>") == 2
    assert "бетон B30" in captured["user"] and "бетон B25" in captured["user"]
    print("OK: текст документа передаётся внутри контейнера недоверенных данных")


def _run_analysis_with(llm_content: str) -> list[dict]:
    """Прогнать анализ на двух реальных чертежах с заданным ответом модели."""

    def fake_post(url, json=None, headers=None, timeout=None):
        system = json["messages"][0]["content"]
        content = ('{"discipline_code": "ОВ", "sheet_name": "План"}'
                   if "штамп" in system else llm_content)
        return _FakeResponse({"message": {"content": content}})

    with patch("app.llm.httpx.post", side_effect=fake_post):
        docs = {}
        for side, name in (("before", "rd_floor1.pdf"), ("after", "rd_floor2_heating.pdf")):
            path = SAMPLE_DIR / name
            with path.open("rb") as f:
                resp = client.post(f"/documents?side={side}",
                                   files={"file": (path.name, f, "application/pdf")})
            assert resp.status_code == 200, resp.text
            docs[side] = resp.json()["id"]

        run = client.post("/analysis-runs", json={
            "before_document_ids": [docs["before"]], "after_document_ids": [docs["after"]]}).json()
        for _ in range(50):
            state = client.get(f"/analysis-runs/{run['id']}").json()
            if state["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        assert state["status"] == "done", state
        return client.get(f"/findings?run_id={run['id']}").json()


def test_findings_come_back_critical_first_with_field_check():
    """Инспектору нужен порядок обхода объекта, а не хронология разбора."""
    findings = _run_analysis_with(
        '{"significant": ['
        '{"label": "H-3", "change": "Отделка санузла 214", "severity": "незначительно",'
        ' "field_check": "Сверить ведомость отделки"},'
        '{"label": "H-1", "change": "Балка Б-3 у оси В смещена на 600мм", "severity": "критично",'
        ' "field_check": "Замерить положение балки по оси В"},'
        '{"label": "H-2", "change": "Воздуховод между осями 3-5", "severity": "существенно",'
        ' "field_check": "Проверить сечение воздуховода"}'
        '], "injection_suspected": false, "noise_note": "",'
        ' "checked_total": 3, "significant_total": 3}'
    )
    assert findings, "анализ не дал ни одной находки"

    order = [f["severity"] for f in findings]
    rank = {"critical": 0, "major": 1, "minor": 2, "": 3}
    assert order == sorted(order, key=lambda s: rank[s]), order
    assert order[0] == "critical", order
    print(f"OK: {len(findings)} находок, критичное первым: {order}")

    critical = next(f for f in findings if f["severity"] == "critical")
    assert "оси В" in critical["change_text"], critical
    assert critical["field_check"], "критичная находка без указания, что делать на объекте"
    assert "алк" in critical["field_check"] or "мер" in critical["field_check"], critical
    print(f"OK: у критичной находки есть действие на объекте — «{critical['field_check']}»")


def test_injection_attempt_becomes_a_critical_finding():
    """Попытка повлиять на анализатор через документ — повод проверить
    добросовестность заявителя, а не техническая ошибка разбора."""
    findings = _run_analysis_with(
        '{"significant": [{"label": "H-1", "change": "Изменён воздуховод",'
        ' "severity": "существенно", "field_check": "Проверить узел"}],'
        ' "injection_suspected": true, "noise_note": "",'
        ' "checked_total": 1, "significant_total": 1}'
    )
    injections = [f for f in findings if "нъекц" in f["label"]]
    assert injections, [f["label"] for f in findings]
    assert all(f["severity"] == "critical" for f in injections), injections
    assert findings[0]["severity"] == "critical", "инъекция должна быть в начале списка"
    assert all(f["field_check"] for f in injections), injections
    print(f"OK: попытка инъекции даёт {len(injections)} критичных находок в начале списка")


def test_stale_database_is_rebuilt_instead_of_crashing():
    """База от прошлой версии не должна ронять первый же запрос: колонок
    меньше, чем в модели, — схема пересобирается."""
    stale_path = "/tmp/nadzor_stale_schema_test.db"
    Path(stale_path).unlink(missing_ok=True)
    stale_engine = create_engine(f"sqlite:///{stale_path}")

    with patch.object(db_module, "engine", stale_engine):
        # База «старой версии»: таблица находок без severity/field_check.
        with stale_engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE findings (id INTEGER PRIMARY KEY, run_id INTEGER, label VARCHAR)"))
        assert db_module._schema_is_stale() is True

        init_db()
        assert db_module._schema_is_stale() is False

        from sqlalchemy import inspect
        columns = {c["name"] for c in inspect(stale_engine).get_columns("findings")}
        assert {"severity", "field_check"} <= columns, columns

    Path(stale_path).unlink(missing_ok=True)
    print("OK: устаревшая схема пересобирается, а не падает на первом запросе")


if __name__ == "__main__":
    test_severity_synonyms_are_normalized()
    test_negated_severity_is_not_read_as_significant()
    test_unknown_severity_is_left_empty_not_guessed()
    test_text_compare_wraps_document_in_untrusted_container()
    test_findings_come_back_critical_first_with_field_check()
    test_injection_attempt_becomes_a_critical_finding()
    test_stale_database_is_rebuilt_instead_of_crashing()
    print("ALL PASS")
