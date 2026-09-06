"""Сквозной тест через настоящий FastAPI-роут: загрузка реальных файлов,
запуск анализа, проверка находок — с замоканной LLM (и для чтения штампа, и
для сравнения листов), чтобы не требовать реального Ollama в CI/песочнице."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

TEST_DB = "/tmp/nadzor_backend_test.db"
Path(TEST_DB).unlink(missing_ok=True)

import os

os.environ["NADZOR_DB_PATH"] = TEST_DB

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

init_db()
client = TestClient(app)

SAMPLE_DIR = Path(
    "/tmp/claude-0/-home-user-DmitryKDI/0870a421-62c2-59a8-8978-c9163f520b16/scratchpad"
)


class _FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_llm_post(url, json=None, headers=None, timeout=None):
    # Г.71 — дефолтный провайдер бэкенда теперь anthropic: системный промпт
    # приходит отдельным полем "system" (не messages[0]), ответ — content-
    # блоками {"content": [{"text": ...}]}, не Ollama-стилем {"message": ...}.
    system = json["system"]
    if "штамп" in system:
        content = '{"discipline_code": "ОВ", "sheet_name": "План этажа"}'
    else:
        content = (
            '{"significant": [{"label": "H-1", "change": "Изменена конфигурация воздуховода"}],'
            ' "noise_note": "", "checked_total": 1, "significant_total": 1}'
        )
    return _FakeResponse({"content": [{"text": content}]})


def upload(side: str, path: Path):
    with path.open("rb") as f:
        resp = client.post(f"/documents?side={side}", files={"file": (path.name, f, "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_pipeline_upload_analyze_findings():
    with patch("app.llm.httpx.post", side_effect=fake_llm_post):
        before_doc = upload("before", SAMPLE_DIR / "pd_tom542_real_name_ОВ.pdf")
        after_doc1 = upload("after", SAMPLE_DIR / "rd_floor1.pdf")
        after_doc2 = upload("after", SAMPLE_DIR / "rd_floor2_heating.pdf")

        assert before_doc["discipline_code"] == "ОВ", before_doc
        assert before_doc["classification_source"] == "filename", before_doc
        assert after_doc1["discipline_code"] == "ОВ", after_doc1
        assert after_doc1["classification_source"] == "stamp_vision", after_doc1
        assert after_doc2["discipline_code"] == "ОВ", after_doc2
        print("OK: uploaded documents classified correctly (filename + vision-stamp fallback)")

        run_resp = client.post(
            "/analysis-runs",
            json={"before_document_ids": [before_doc["id"]], "after_document_ids": [after_doc1["id"], after_doc2["id"]]},
        )
        assert run_resp.status_code == 200, run_resp.text
        run_id = run_resp.json()["id"]

        run = None
        for _ in range(30):
            run = client.get(f"/analysis-runs/{run_id}").json()
            if run["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        assert run["status"] == "done", run
        # Не фиксированное число: pd_tom542 (177 стр.) реально содержит и
        # текстовые страницы, и вклеенные чертежи большого формата (см.
        # test_page_kind.py) — сколько именно страниц распознается как
        # чертёж, определяется реальным содержимым файла, не тестом.
        assert run["pairs_total"] > 0, run
        print(f"OK: analysis run completed, {run['pairs_total']} pairs processed")

        # "Данные о работе ИИ" — какой провайдер считал и сколько пар реально
        # дошло до ответа, а не просто "готово" без деталей.
        assert run["provider"] and run["model"], run
        assert run["pairs_llm_ok"] == run["pairs_total"], run
        assert run["pairs_llm_error"] == 0, run
        pairs = client.get(f"/analysis-runs/{run_id}/pairs").json()
        assert len(pairs) == run["pairs_total"], pairs
        assert all(p["llm_status"] == "ok" for p in pairs), pairs
        print(f"OK: run reports provider «{run['provider']}» / «{run['model']}», "
              f"all {run['pairs_llm_ok']} pairs reached the LLM successfully")

        findings = client.get(f"/findings?run_id={run_id}").json()
        vision_findings = [f for f in findings if f["kind"] == "vision"]
        # Оба after-файла — чертежи (rd_floor1/rd_floor2_heating), поэтому
        # сопоставление идёт только внутри пула чертежей — все находки vision.
        assert len(vision_findings) == run["pairs_total"], findings  # один significant item на пару (мок)
        for f in vision_findings:
            assert "воздуховод" in f["change_text"].lower()
            assert f["after_document_id"] is not None and f["after_page"] is not None
        print(f"OK: {len(vision_findings)} vision findings recorded through the real API, each with a page image reference")

        patch_resp = client.patch(f"/findings/{vision_findings[0]['id']}", json={"reviewed_status": "confirmed"})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reviewed_status"] == "confirmed"
        print("OK: finding review status can be updated via API")


def test_llm_failure_is_visible_not_silent():
    """Раньше упавший вызов ИИ и "ИИ честно ничего не нашёл" выглядели в
    ответе API совершенно одинаково — 0 findings, run.status == "done".
    Инспектору нужно различать эти два случая (см. запрос сессии: "мне надо
    как-то получить данные о работе с ИИ, а не просто «не найдено»") —
    здесь провайдер ломается на каждом вызове, и это должно быть видно и в
    сводке прогона, и по каждой паре листов."""

    def broken_llm_post(*a, **kw):
        raise ConnectionError("провайдер недоступен (симуляция для теста)")

    with patch("app.llm.httpx.post", side_effect=broken_llm_post):
        before_doc = upload("before", SAMPLE_DIR / "rd_floor1.pdf")
        after_doc = upload("after", SAMPLE_DIR / "rd_floor2_heating.pdf")

        run_resp = client.post(
            "/analysis-runs",
            json={"before_document_ids": [before_doc["id"]], "after_document_ids": [after_doc["id"]]},
        )
        run_id = run_resp.json()["id"]

        run = None
        for _ in range(30):
            run = client.get(f"/analysis-runs/{run_id}").json()
            if run["status"] in ("done", "error"):
                break
            time.sleep(0.2)
        # Сбой отдельных вызовов ИИ не должен ронять весь прогон — деградация
        # до "проверок не было", а не 500 или зависший статус (см. main.py).
        assert run["status"] == "done", run
        assert run["pairs_total"] > 0, run
        assert run["pairs_llm_error"] == run["pairs_total"], run
        assert run["pairs_llm_ok"] == 0, run

        pairs = client.get(f"/analysis-runs/{run_id}/pairs").json()
        assert len(pairs) == run["pairs_total"], pairs
        assert all(p["llm_status"] == "error" for p in pairs), pairs
        assert all("недоступен" in (p["llm_error"] or "") for p in pairs), pairs

        findings = client.get(f"/findings?run_id={run_id}").json()
        assert findings == [], findings
        print(f"OK: все {run['pairs_llm_error']} пар с сорванным вызовом ИИ видны в сводке и по каждой "
              f"паре отдельно — «0 находок» здесь не перепутать с «ИИ ничего не нашёл»")


def test_page_image_endpoint_serves_real_png():
    with patch("app.llm.httpx.post", side_effect=fake_llm_post):
        doc = upload("before", SAMPLE_DIR / "rd_floor1.pdf")
    resp = client.get(f"/page-image/{doc['id']}/1")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(resp.content) > 5000
    print("OK: /page-image serves a real PNG for an uploaded document")

    bad = client.get(f"/page-image/{doc['id']}/99")
    assert bad.status_code == 404
    print("OK: /page-image returns 404 for an out-of-range page instead of crashing")


def test_settings_roundtrip():
    """Г.71 — провайдер должен быть из реально поддерживаемого набора
    (anthropic/gigachat), иначе следующий реальный вызов ИИ (в этом же
    тестовом процессе, той же БД) получит ValueError: unknown provider —
    этот тест сам был реальным источником такой порчи до фикса: ставил
    "local", который backend больше не принимает, и не возвращал дефолт
    обратно, ломая порядко-зависимые прогоны с test_findings_quality.py."""
    try:
        resp = client.put("/settings", json={"provider": "gigachat", "base_url": "", "model": "GigaChat-2-Pro"})
        assert resp.status_code == 200
        got = client.get("/settings").json()
        assert got["provider"] == "gigachat"
        assert got["model"] == "GigaChat-2-Pro"
        print("OK: settings roundtrip through API")
    finally:
        client.put("/settings", json={"provider": "anthropic", "base_url": "", "model": ""})


if __name__ == "__main__":
    test_full_pipeline_upload_analyze_findings()
    test_llm_failure_is_visible_not_silent()
    test_page_image_endpoint_serves_real_png()
    test_settings_roundtrip()
    print("ALL PASS")
