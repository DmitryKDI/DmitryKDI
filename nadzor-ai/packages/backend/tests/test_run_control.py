"""ОСТАНОВКА прогона и продолжение после перезапуска сервера (Г.114).

Раньше запущенное нельзя было остановить вообще: ошибся комплектом — жди
конца или перезапускай сервер. Перезапуск делал хуже: работа обрывалась, а
запись оставалась «идёт», и полоса прогресса показывала ход у мёртвого
прогона — состояние, из которого нет выхода и по которому нельзя понять,
что случилось.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, run_control  # noqa: E402
from app.db import get_session, init_db  # noqa: E402

init_db()


@pytest.fixture(autouse=True)
def clean_flags():
    for kind, run_id in run_control.pending():
        run_control.clear(kind, run_id)
    yield
    for kind, run_id in run_control.pending():
        run_control.clear(kind, run_id)


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _running_pd_run() -> int:
    db = next(get_session())
    try:
        run = models.PdRun(status="running", document_ids=[], side="before")
        db.add(run)
        db.commit()
        db.refresh(run)
        return run.id
    finally:
        db.close()


def test_check_raises_only_after_the_request():
    assert run_control.is_requested(run_control.KIND_PD, 777) is False
    run_control.check(run_control.KIND_PD, 777)  # молчит
    run_control.request(run_control.KIND_PD, 777)
    with pytest.raises(run_control.RunCancelled):
        run_control.check(run_control.KIND_PD, 777)
    run_control.clear(run_control.KIND_PD, 777)
    run_control.check(run_control.KIND_PD, 777)
    print("OK: точка остановки срабатывает только после просьбы")


def test_cancel_endpoint_marks_the_run_and_raises_at_the_next_checkpoint():
    run_id = _running_pd_run()
    response = _client().post(f"/pd-runs/{run_id}/cancel")
    assert response.status_code == 200, response.text
    body = response.json()
    # Формулировка проверяется намеренно: остановка не мгновенная, и
    # молчание об этом делало бы кнопку «сломанной» на вид.
    assert "ближайшей безопасной точке" in body["detail"], body
    with pytest.raises(run_control.RunCancelled):
        run_control.check(run_control.KIND_PD, run_id)

    db = next(get_session())
    try:
        run = db.get(models.PdRun, run_id)
        assert run.cancelled_at is not None, "просьба не записана в прогон"
        assert run.status == "running", "статус меняет исполнитель, а не кнопка"
    finally:
        db.close()
    print("OK: кнопка просит остановиться и говорит, что это не мгновенно")


def test_cancelling_a_finished_run_says_so_instead_of_pretending():
    db = next(get_session())
    try:
        run = models.PdRun(status="done", document_ids=[], side="before")
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()
    body = _client().post(f"/pd-runs/{run_id}/cancel").json()
    assert body["status"] == "done", body
    assert "уже завершил" in body["detail"], body
    print("OK: остановка завершённого прогона не выдаётся за остановку")


def test_every_run_kind_can_be_stopped():
    """Кнопка «стоп» должна быть у каждого длинного дела, а не у одного."""
    client = _client()
    for path in ("/pd-runs", "/compliance-runs", "/analysis-runs", "/triangulated-runs"):
        response = client.post(f"{path}/999999/cancel")
        assert response.status_code == 404, (path, response.status_code)
    print("OK: остановка есть у разбора, сверки, анализа и карты внимания")


def test_interrupted_runs_are_restarted_after_a_server_restart():
    from app import main

    run_id = _running_pd_run()
    started: list[int] = []
    with patch.dict(main._RUN_WORKERS,
                    {run_control.KIND_PD: (models.PdRun, started.append)}):
        resumed = main.resume_interrupted_runs()
    assert f"pd#{run_id}" in resumed, resumed
    # Поток запускается фактически: без этого «продолжение» было бы записью
    # в базе, а не работой.
    for _ in range(200):
        if started:
            break
        import time
        time.sleep(0.01)
    assert started == [run_id], started

    db = next(get_session())
    try:
        run = db.get(models.PdRun, run_id)
        assert run.units_done == 0, "ход считается заново, а не продолжает старый"
        assert "перезапуск" in run.stage, run.stage
    finally:
        db.close()
    print("OK: оборванный перезапуском прогон запускается заново и говорит об этом")


def test_a_run_stopped_by_the_inspector_is_not_restarted():
    """Просьбу прекратить перезапуск сервера не отменяет."""
    from app import main
    import datetime as dt

    db = next(get_session())
    try:
        run = models.PdRun(status="running", document_ids=[], side="before",
                           cancelled_at=dt.datetime.utcnow())
        db.add(run)
        db.commit()
        run_id = run.id
    finally:
        db.close()

    with patch.dict(main._RUN_WORKERS,
                    {run_control.KIND_PD: (models.PdRun, lambda rid: pytest.fail("запущен"))}):
        resumed = main.resume_interrupted_runs()
    assert f"pd#{run_id}" not in resumed, resumed

    db = next(get_session())
    try:
        assert db.get(models.PdRun, run_id).status == "cancelled"
    finally:
        db.close()
    print("OK: остановленный инспектором прогон после перезапуска не оживает")


def test_resume_can_be_switched_off(monkeypatch):
    from app import main

    _running_pd_run()
    monkeypatch.setenv(main.RESUME_RUNS_ENV, "0")
    assert main.resume_interrupted_runs() == []
    print("OK: продолжение прогонов отключается переменной окружения")
