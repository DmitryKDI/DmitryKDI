"""scripts/start-all.sh's --reload flags actually start the process.

Real bug found live: --reload-exclude took an ABSOLUTE path
($ROOT/packages/backend/*). Uvicorn's reload-pattern resolver globs against
the current working directory and raises NotImplementedError on a
non-relative pattern -- the site server (port 8000) crashed on every launch
through the real script, while every other check in this test suite used a
direct uvicorn invocation without --reload and never exercised this path.
Nothing short of running uvicorn with the script's actual flags catches this.
"""
from __future__ import annotations

import re
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _extract_uvicorn_args(script_text: str, marker: str, port: int) -> list[str]:
    r"""Pull one `uvicorn ... \` continuation block out of start-all.sh and
    turn it into an argv list -- the same flags the script itself passes,
    minus $ROOT expansion (kept relative on purpose, see the flags below) and
    with the port swapped out so this test doesn't collide with a real run."""
    start = script_text.index(marker)
    end = script_text.index(">", start)
    block = script_text[start:end].replace("\\\n", " ").replace('"', "")
    tokens = [t for t in re.split(r"\s+", block) if t]
    out = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok == "--port":
            out += ["--port", str(port)]
            skip_next = True
            continue
        out.append(tok.replace("$ROOT", str(ROOT)))
    return out


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_site_uvicorn_command_starts_without_crashing(tmp_path):
    script = (ROOT / "scripts/start-all.sh").read_text(encoding="utf-8")
    # Не просто "uvicorn api.main:app" — эта подстрока встречается раньше,
    # внутри pkill-команды освобождения портов; нужен именно вызов uvicorn.
    args = _extract_uvicorn_args(script, "uvicorn api.main:app --host", port=8991)
    assert "--reload-exclude" in args, "тест устарел вместе со скриптом — проверьте флаги вручную"
    assert not any(a.startswith(str(ROOT)) and "*" in a for a in args), \
        "--reload-exclude с абсолютным путём с маской падает у uvicorn (см. описание файла)"

    port = int(args[args.index("--port") + 1])
    assert _port_free(port), f"порт {port} занят — тест не может проверить запуск"

    db_path = tmp_path / "test.db"
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", *args],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={"PYTHONPATH": str(ROOT / "packages"), "APP_ROOT": str(ROOT),
             "PATH": "/usr/bin:/bin",
             "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
             "STORAGE_ROOT": str(tmp_path / "storage")},
    )
    try:
        # Первый запуск разбирает демо-комплект в свежую базу — заметно
        # дольше обычного старта (см. комментарий в самом start-all.sh).
        deadline = time.monotonic() + 150
        started = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            if not _port_free(port):
                started = True
                break
            time.sleep(0.5)
        if not started:
            proc.terminate()
            out = proc.communicate(timeout=10)[0]
            raise AssertionError(f"сервер не поднялся с флагами --reload скрипта:\n{out[-3000:]}")
        print(f"OK: uvicorn с флагами start-all.sh (включая --reload-exclude) поднялся на :{port}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_browser_launch_url_is_not_static():
    """Реальный баг с батником: Chrome/Edge, получив от `start URL` адрес,
    который уже открыт в одной из вкладок, переключается на неё вместо
    новой загрузки — пользователь видел код, каким он был при прошлом
    запуске батника, а не только что подтянутый git reset --hard. Статичный
    http://localhost:5173 без переменной части наступает на эти грабли при
    каждом повторном запуске."""
    script = (ROOT / "scripts/start-all.sh").read_text(encoding="utf-8")
    marker = "cmd.exe /c start"
    start = script.index(marker)
    line = script[start:script.index("\n", start)]
    assert "$(" in line or "${" in line, \
        "адрес автозапуска браузера должен меняться от запуска к запуску, иначе Chrome откроет старую вкладку"
    print("OK: адрес автозапуска не статичен — новая вкладка на каждый запуск батника")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        test_site_uvicorn_command_starts_without_crashing(Path(d))
    test_browser_launch_url_is_not_static()
    print("ALL PASS")
