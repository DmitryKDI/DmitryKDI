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
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def fake_llm_post(url, json=None, headers=None, timeout=None):
    system = json["messages"][0]["content"]
    if "штамп" in system:
        content = '{"discipline_code": "ОВ", "sheet_name": "План этажа"}'
    else:
        content = (
            '{"significant": [{"label": "H-1", "change": "Изменена конфигурация воздуховода"}],'
            ' "noise_note": "", "checked_total": 1, "significant_total": 1}'
        )
    return _FakeResponse({"choices": [{"message": {"content": content}}]})


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
    resp = client.put("/settings", json={"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3:8b"})
    assert resp.status_code == 200
    got = client.get("/settings").json()
    assert got["provider"] == "local"
    assert got["model"] == "qwen3:8b"
    print("OK: settings roundtrip through API")


if __name__ == "__main__":
    test_full_pipeline_upload_analyze_findings()
    test_page_image_endpoint_serves_real_png()
    test_settings_roundtrip()
    print("ALL PASS")
