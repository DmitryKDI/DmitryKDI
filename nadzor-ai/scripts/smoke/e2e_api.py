"""Сквозная проверка запуска через внешний API (/api/v1).

Проходит весь путь так, как его пройдёт внешняя система: загрузка комплекта
с реестром → process_id → опрос статуса → протокол → решение инспектора →
финализация → выгрузки → пакет для внешней системы. Каждое ожидание
проверяется явно; первое несовпадение останавливает прогон с кодом 1.

Документы синтетические и создаются здесь же: реквизитов реальных объектов
в проверке нет. Сравнение по существу выполняет модель; с дублёром
(`fake_model.py`) проверяется механика, а не качество.

    python scripts/smoke/e2e_api.py --base-url http://127.0.0.1:8010
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
import pymupdf

FONT_PATHS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf")
LINES = {
    "PD": ["Пояснительная записка. Технико-экономические показатели.",
           "Площадь застройки: 1200 м2",
           "Этажность надземная: 9",
           "Класс прочности бетона монолитных конструкций: B30"],
    "RD": ["Общие данные. Таблица ТЭП.",
           "Площадь застройки: 1250 м2",
           "Этажность надземная: 9",
           "Класс прочности бетона монолитных конструкций: B25"],
}


def _font() -> str:
    for path in FONT_PATHS:
        if Path(path).is_file():
            return path
    sys.exit("нужен шрифт DejaVuSans с кириллицей (пакет fonts-dejavu-core)")


def _pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=_font())
    for index, line in enumerate(lines):
        page.insert_text((60, 80 + 22 * index), line, fontname="F0", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _docx(lines: list[str]) -> bytes:
    from docx import Document

    document = Document()
    for line in lines:
        document.add_paragraph(line)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _row(name: str, stage: str) -> dict:
    return {"file_id": name, "file_name": name, "object_id": "SMOKE-OBJECT",
            "doc_stage": stage, "document_code": f"XXX/000000/1-{stage}", "revision": "1",
            "approval_status": "APPROVED", "approval_date": "2026-01-01"}


class Check:
    def __init__(self) -> None:
        self.passed = 0

    def __call__(self, condition: bool, message: str) -> None:
        if not condition:
            print(f"  ✗ {message}")
            sys.exit(1)
        self.passed += 1
        print(f"  ✓ {message}")


def run(base: str, timeout: float, login: str, password: str) -> None:
    check = Check()
    client = httpx.Client(base_url=base, timeout=120)

    print("1. Готовность сервиса и вход")
    check(client.get("/health").json().get("status") == "ok", "/health отвечает")
    check(client.get("/api/v1/processes/1/status").status_code == 401,
          "без входа API закрыто")
    session = client.post("/api/v1/auth/login", json={"login": login, "password": password})
    check(session.status_code == 200, f"вход «{login}» ({session.status_code})")
    client.headers["Authorization"] = f"Bearer {session.json()['token']}"
    llm = client.get("/llm-check").json()
    check(llm["reachable"], f"модель отвечает: {llm['message']}")
    schema = client.get("/openapi.json").json()
    check(schema["openapi"].startswith("3."), f"OpenAPI {schema['openapi']}")

    print("2. Загрузка комплекта с реестром")
    files = {"pd.pdf": ("PD", _pdf(LINES["PD"])), "rd.pdf": ("RD", _pdf(LINES["RD"])),
             "id-act.docx": ("ID", _docx(["Акт освидетельствования скрытых работ.",
                                           "Класс прочности бетона монолитных конструкций: B25"]))}
    registry = json.dumps([_row(name, stage) for name, (stage, _) in files.items()])
    started = time.monotonic()
    response = client.post("/api/v1/documents/upload", files=[
        *(("files", (name, data, "application/octet-stream"))
          for name, (_, data) in files.items()),
        ("registry", ("registry.json", registry.encode(), "application/json")),
    ])
    check(response.status_code == 200, f"загрузка принята ({response.status_code})")
    body = response.json()
    pid = body["process_id"]
    check(not body["rejected"], "ни один файл не отклонён")
    check(body["status"] == "PENDING", "статус после загрузки PENDING")
    check(body["scenario"] == "FULL", "сценарий FULL (ПД, РД и ИД)")

    print(f"3. Проверка процесса {pid}")
    status = {}
    while time.monotonic() - started < timeout:
        status = client.get(f"/api/v1/processes/{pid}/status").json()
        if status["status"] not in ("PENDING", "PARSING"):
            break
        time.sleep(1)
    elapsed = time.monotonic() - started
    check(status.get("status") in ("READY", "VERIFYING", "COMPLETED"),
          f"протокол сформирован: {status.get('status')} за {elapsed:.1f} с")

    process = client.get(f"/api/v1/processes/{pid}").json()
    proto = process["protocol"]
    check(set(proto["tables"]) == {"completeness", "candidates", "confirmed_violations",
                                   "negative_verified", "suspicions"}, "пять таблиц протокола")
    check(proto["upload_status"] == {"PD": "PD_UPLOADED", "RD": "RD_UPLOADED",
                                     "ID": "ID_UPLOADED"}, "статусы загрузки по стадиям")
    versions = proto["versions"]
    check(all(versions.get(key) for key in ("matrix_version", "model_version",
                                           "input_manifest_hash")), "версии в протоколе")
    candidates = proto["tables"]["candidates"]
    codes = {card["parameter_code"] for card in candidates}
    check({"M-001", "M-055"} <= codes, f"кандидаты по различающимся параметрам: {sorted(codes)}")
    negatives = {card["parameter_code"] for card in proto["tables"]["negative_verified"]}
    check("M-007" in negatives, "совпадающий параметр — предварительный NEGATIVE_VERIFIED")
    card = next(card for card in candidates if card["parameter_code"] == "M-001")
    check(card["delta"] == 50.0, f"delta по площади: {card['delta']}")
    sources = card["sources"]
    check({source["stage"] for source in sources} >= {"PD", "RD"}, "источники ПД и РД")
    check(all(source["bbox_polygon"] and source["sha256"] for source in sources),
          "у каждого источника координаты и SHA-256")
    check(all(source["document_code"] and source["revision"] for source in sources),
          "у каждого источника шифр и редакция")

    print("4. Решения инспектора и финализация")
    version = process["version"]
    early = client.post(f"/api/v1/processes/{pid}/finalize", json={})
    check(early.status_code == 409, "финализация без решений запрещена")
    for card in candidates:
        decision = client.post(f"/api/v1/processes/{pid}/decisions", json={
            "finding_id": card["finding_id"], "status": "CONFIRMED_VIOLATION",
            "reason": "подтверждено по доказательствам",
            "expected_version": version})
        check(decision.status_code == 200, f"решение по {card['finding_id']}")
        version = decision.json()["version"]
    final = client.post(f"/api/v1/processes/{pid}/finalize", json={}).json()
    check(final["status"] == "FINALIZED", "протокол финализирован")
    late = client.post("/api/v1/documents/upload", data={"process_id": str(pid)},
                       files=[("files", ("x.pdf", _pdf(["x"]), "application/pdf"))])
    after = client.get(f"/api/v1/processes/{pid}").json()
    check(late.status_code == 200 and "notice" in late.json()
          and after["status"] == "FINALIZED" and after["pending_documents"],
          "после финализации проверка не запускается, инспектор уведомлён (ТЗ 9.6)")

    print("5. Выгрузки")
    for fmt, magic in (("json", b"{"), ("xml", b"<?xml"), ("docx", b"PK"), ("pdf", b"%PDF")):
        export = client.get(f"/api/v1/processes/{pid}/export", params={"format": fmt})
        check(export.status_code == 200 and export.content.startswith(magic),
              f"протокол в {fmt.upper()} ({len(export.content)} байт)")
    sent = client.post(f"/api/v1/inspection/{pid}").json()
    check(len(sent["confirmed_violations"]) == len(candidates),
          "пакет для внешней системы — только подтверждённое")

    print(f"\nГОТОВО: {check.passed} проверок пройдено, процесс {pid}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="сколько ждать протокол, секунд")
    parser.add_argument("--login", default=os.environ.get("NADZOR_ADMIN_LOGIN", "admin"))
    parser.add_argument("--password", default=os.environ.get("NADZOR_ADMIN_PASSWORD", ""))
    args = parser.parse_args()
    run(args.base_url, args.timeout, args.login, args.password)


if __name__ == "__main__":
    main()
