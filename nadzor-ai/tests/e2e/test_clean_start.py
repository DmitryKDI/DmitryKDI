"""Чистый старт: система не создаёт вымышленных объектов сама по себе.

Демо-комплект — витрина для показа, а не рабочий режим: пользователь,
запустивший систему у себя, должен видеть в журналах только то, что загрузил
сам. Общая фикстура `client` намеренно включает SEED_DEMO_OBJECTS=1 (иначе
сквозным тестам не на чем проверять RBAC), поэтому поведение по умолчанию
проверяется здесь.

Приложение поднимается В ОТДЕЛЬНОМ ПРОЦЕССЕ: api.db читает DATABASE_URL на
импорте, а фикстура `client` — session-scoped и уже держит поднятое
приложение со своей базой. Перезагрузка модулей внутри общего процесса ломала
именно её (проверено: 6 упавших тестов в соседних файлах), поэтому изоляция
здесь обязательна, а не перестраховка.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CHILD = r'''
import os, sys, json
sys.path.insert(0, r"{root}/packages")
sys.path.insert(0, r"{root}")
os.environ["APP_ROOT"] = r"{root}"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + r"{db}"
os.environ["STORAGE_ROOT"] = r"{storage}"
os.environ.pop("SEED_DEMO_OBJECTS", None)   # поведение по умолчанию

import api.main as main
from fastapi.testclient import TestClient

with TestClient(main.app) as client:
    token = client.post("/api/auth/login", json={{"subject": "sudir:77005"}}).json()
    headers = {{"Authorization": "Bearer " + token["access_token"]}}
    out = {{
        "objects": client.get("/api/objects", headers=headers).json()["items"],
        "dashboard": client.get("/api/dashboard", headers=headers).json(),
        "users": client.get("/api/admin/users", headers=headers).json()["items"],
    }}
print("RESULT_JSON:" + json.dumps(out, ensure_ascii=False))
'''


def test_no_demo_objects_without_the_flag(tmp_path):
    script = _CHILD.format(root=ROOT, db=tmp_path / "clean.db", storage=tmp_path / "storage")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          cwd=ROOT, timeout=300, env={**os.environ, "PYTHONPATH": str(ROOT / "packages")})
    assert proc.returncode == 0, f"приложение не поднялось на чистой базе:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"

    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON:"))
    data = __import__("json").loads(line[len("RESULT_JSON:"):])

    assert data["objects"] == [], "система создала объекты, которых никто не заводил"
    assert data["dashboard"]["objects_total"] == 0, data["dashboard"]
    assert data["dashboard"]["findings_total"] == 0, data["dashboard"]
    # Сотрудники нужны всегда: без них роли в интерфейсе не с чем сопоставить,
    # а экран "Пользователи и роли" пуст.
    assert data["users"], "сотрудники должны заводиться независимо от демо-объектов"
    print(f"OK: чистый старт — объектов 0, сотрудников {len(data['users'])}, дашборд не падает на нулях")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_no_demo_objects_without_the_flag(Path(d))
    print("ALL PASS")
