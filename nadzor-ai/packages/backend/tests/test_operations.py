"""Эксплуатация: логи, метрики, целостность, резервные копии, антивирус (ТЗ 12–13)."""
import datetime as dt
import json
import logging
import socket
import sqlite3
import threading

import pytest
from app import antivirus, backup, file_store, integrity, main, observability
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_every_response_carries_request_id():
    fresh = client.get("/health")
    assert len(fresh.headers["X-Request-ID"]) >= 16
    echoed = client.get("/health", headers={"X-Request-ID": "ext-42"})
    assert echoed.headers["X-Request-ID"] == "ext-42"
    forged = client.get("/health", headers={"X-Request-ID": "bad\nline injected"})
    assert forged.headers["X-Request-ID"] != "bad\nline injected"
    print("OK: request_id принимается от внешней системы или назначается сервисом")


def test_log_line_is_json_with_required_fields():
    record = logging.LogRecord("inspector", logging.INFO, __file__, 1, "загрузка", None, None)
    record.user_id = "7"
    token = observability.request_id_var.set("req-1")
    try:
        line = json.loads(observability.JsonFormatter().format(record))
    finally:
        observability.request_id_var.reset(token)
    for field in ("timestamp", "level", "service", "message", "request_id", "user_id"):
        assert field in line, field
    assert line["request_id"] == "req-1" and line["user_id"] == "7"
    print("OK: строка лога — JSON с обязательными полями ТЗ 13")


def test_metrics_are_exposed_for_prometheus(act_as):
    client.get("/health")
    act_as("inspector")
    assert client.get("/metrics").status_code == 403
    act_as("service")
    body = client.get("/metrics").text
    for name in ("inspector_http_requests_total", "inspector_http_5xx_total",
                 "inspector_http_request_duration_seconds_bucket", "process_cpu_seconds_total",
                 "process_resident_memory_bytes", "inspector_disk_free_bytes",
                 "inspector_active_sessions", "inspector_queue_size",
                 "inspector_integrity_failures"):
        assert name in body, name
    print("OK: метрики производительности и сессий отдаются в формате Prometheus")


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(file_store, "STORE_PATH", tmp_path / "files.db")
    monkeypatch.setattr(file_store, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


def test_integrity_check_finds_tampered_file(isolated_store):
    good = file_store.put(b"%PDF-1.4 good")
    bad = file_store.put(b"%PDF-1.4 will be tampered")
    file_store.materialize(good)
    assert integrity.run_check()["status"] == "OK"
    with sqlite3.connect(isolated_store / "files.db") as db:
        db.execute("UPDATE files SET content = ? WHERE digest = ?", (b"%PDF-1.4 evil", bad))
    (isolated_store / "cache" / f"{good}.pdf").write_bytes(b"corrupted cache")
    result = integrity.run_check()
    assert result["status"] == "FAILED"
    assert {item["digest"] for item in result["failures"]} == {good, bad}
    assert not (isolated_store / "cache" / f"{good}.pdf").exists(), "испорченный кэш оставлен"
    print("OK: подмена оригинала и порча кэша обнаружены проверкой контрольных сумм")


def test_backups_are_taken_on_schedule_and_pruned(tmp_path, monkeypatch):
    monkeypatch.setenv("NADZOR_BACKUP_DIR", str(tmp_path / "backups"))
    now = dt.datetime(2026, 3, 1, 12, 0)
    assert backup.due(now) == ["frequent", "daily"]
    assert backup.due(now + dt.timedelta(minutes=5)) == []
    assert backup.due(now + dt.timedelta(minutes=15)) == ["frequent"]
    daily = next((tmp_path / "backups" / "daily").iterdir())
    with sqlite3.connect(daily / "nadzor.db") as db:
        assert db.execute("SELECT count(*) FROM official_runs").fetchone()[0] >= 0
    later = now + dt.timedelta(days=31)
    backup.due(later)
    stamps = [path.name for path in (tmp_path / "backups" / "daily").iterdir()]
    assert daily.name not in stamps, "копия старше 30 дней не удалена"
    assert backup.status()["daily"]["count"] == 1
    print("OK: копии каждые 15 минут и ежедневно; старше срока удаляются")


def test_backup_is_disabled_without_directory(monkeypatch):
    monkeypatch.delenv("NADZOR_BACKUP_DIR", raising=False)
    assert backup.due() == [] and backup.status()["enabled"] is False
    print("OK: без каталога копирование выключено, и это видно")


def _fake_clamd(reply: bytes) -> int:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def serve():
        conn, _ = server.accept()
        with conn:
            data = b""
            while not data.endswith(b"\0\0\0\0"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            conn.sendall(reply)
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_antivirus_verdicts(monkeypatch):
    monkeypatch.delenv(antivirus.HOST_ENV, raising=False)
    assert antivirus.scan(b"x").status == antivirus.NOT_CONFIGURED
    monkeypatch.setenv(antivirus.HOST_ENV, "127.0.0.1")
    monkeypatch.setenv(antivirus.PORT_ENV, str(_fake_clamd(b"stream: OK\0")))
    assert antivirus.scan(b"%PDF-1.4").status == antivirus.CLEAN
    monkeypatch.setenv(antivirus.PORT_ENV, str(_fake_clamd(b"stream: Eicar-Signature FOUND\0")))
    infected = antivirus.scan(b"%PDF-1.4")
    assert infected.status == antivirus.INFECTED and "Eicar" in infected.detail
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    monkeypatch.setenv(antivirus.PORT_ENV, str(port))
    assert antivirus.scan(b"%PDF-1.4").status == antivirus.UNAVAILABLE
    print("OK: чистый, заражённый и недоступный антивирус различаются")


def test_upload_is_refused_when_infected_or_scanner_down(monkeypatch):
    monkeypatch.setattr(antivirus, "scan", lambda data: antivirus.ScanResult(
        antivirus.INFECTED, "Test-Signature"))
    infected = client.post("/documents?side=before",
                           files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert infected.status_code == 422 and "заражён" in infected.text
    monkeypatch.setattr(antivirus, "scan", lambda data: antivirus.ScanResult(
        antivirus.UNAVAILABLE, "антивирус недоступен"))
    down = client.post("/documents?side=before",
                       files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert down.status_code == 503
    assert main.scan_or_reject is not None
    print("OK: заражённый файл отклонён; без работающего антивируса файл не принимается")
