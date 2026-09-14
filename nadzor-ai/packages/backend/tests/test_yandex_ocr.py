"""Yandex Vision OCR: настройка, запрос и видимый технический сбой."""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import documents, facts_digest, yandex_ocr  # noqa: E402
from app.documents import DocumentFacts  # noqa: E402


def _blank_page():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    return doc, page


def test_yandex_request_does_not_enable_provider_logging(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"textAnnotation": {"fullText": "Распознанный текст"}}}

    def fake_post(url, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(yandex_ocr.httpx, "post", fake_post)
    doc, page = _blank_page()
    try:
        result = yandex_ocr.recognize_page(
            page, yandex_ocr.YandexOcrConfig(api_key="secret", folder_id="folder"))
    finally:
        doc.close()
    assert result.text == "Распознанный текст" and not result.error
    assert captured["headers"]["x-data-logging-enabled"] == "false"
    assert captured["headers"]["Authorization"] == "Api-Key secret"
    assert captured["json"]["model"] == "page"
    assert captured["json"]["content"]
    print("OK: OCR отправляет страницу с отключённым логированием данных")


def test_textless_page_uses_ocr_and_marks_source(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    doc, _ = _blank_page()
    doc.save(path)
    doc.close()
    cfg = yandex_ocr.YandexOcrConfig(api_key="secret", folder_id="folder")
    monkeypatch.setattr(documents, "load_config", lambda: cfg)
    monkeypatch.setattr(
        documents, "recognize_page",
        lambda page, config: yandex_ocr.YandexOcrResult(text="Помещение 101"),
    )
    facts = documents.extract_document_facts(str(path), "скан")
    assert facts.ocr_status == "done"
    assert facts.ocr_pages_done == [1] and facts.ocr_text_pages == [1]
    assert facts.text_facts[0]["text"] == "Помещение 101"
    print("OK: страница без текстового слоя распознаётся и помечается как OCR")


def test_ocr_failure_is_visible_in_digest():
    facts = DocumentFacts(
        name="скан", pages=2, text_facts=[], room_facts=[],
        page_kinds={1: "drawing", 2: "drawing"}, ocr_status="error",
        ocr_pages_total=2, ocr_pages_done=[1], ocr_errors={2: "HTTP 429"},
    )
    summary = facts_digest.digest_of(facts)["ocr"]
    assert summary["status"] == "error"
    assert summary["pages_done"] == 1
    assert "ошибок: 1" in summary["message"]
    print("OK: сбой OCR виден и не называется отсутствием текста")

