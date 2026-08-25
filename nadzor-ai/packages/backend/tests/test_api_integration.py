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
        assert run["pairs_total"] == 2, run
        print(f"OK: analysis run completed, {run['pairs_total']} pairs processed")

        findings = client.get(f"/findings?run_id={run_id}").json()
        vision_findings = [f for f in findings if f["kind"] == "vision"]
        assert len(vision_findings) == 2, findings  # один significant item на каждую из 2 пар
        for f in vision_findings:
            assert "воздуховод" in f["change_text"].lower()
        print(f"OK: {len(vision_findings)} vision findings recorded through the real API")

        patch_resp = client.patch(f"/findings/{vision_findings[0]['id']}", json={"reviewed_status": "confirmed"})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["reviewed_status"] == "confirmed"
        print("OK: finding review status can be updated via API")


def test_settings_roundtrip():
    resp = client.put("/settings", json={"provider": "local", "base_url": "http://localhost:11434/v1", "model": "qwen3:8b"})
    assert resp.status_code == 200
    got = client.get("/settings").json()
    assert got["provider"] == "local"
    assert got["model"] == "qwen3:8b"
    print("OK: settings roundtrip through API")


if __name__ == "__main__":
    test_full_pipeline_upload_analyze_findings()
    test_settings_roundtrip()
    print("ALL PASS")
