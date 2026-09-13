"""Bounded local blind-run automation for NADZOR.AI.

The script deliberately keeps benchmark ground truth OUTSIDE the runtime.  It
can automate: git sync -> clean workspace/LLM cache -> backend -> upload PDFs ->
lean triangulated run -> collect logs -> GigaChat review -> external evaluator
-> optional coding-agent hook -> repeat.

The evaluator and coding agent are external commands.  This is important: the
runtime never receives expected rooms/sheets/findings, while an external scorer
may know ground truth.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
BACKEND = ROOT / "packages" / "backend"
RUN_LOG = ROOT / "data" / "run_logs" / "tasks" / "triangulated" / "latest.json"
PEER_REVIEW = ROOT / "run_logs" / "gigachat_peer_review_latest.json"
ITERATIONS = ROOT / "run_logs" / "autoloop"


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(shlex.quote(str(x)) for x in cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, check=check)


def git_clean() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def sync_git(branch: str) -> None:
    if not git_clean():
        raise RuntimeError("working tree is dirty; autoloop refuses to pull over local changes")
    run(["git", "fetch", "origin"], cwd=REPO)
    run(["git", "checkout", branch], cwd=REPO)
    run(["git", "pull", "--ff-only", "origin", branch], cwd=REPO)


def prepare_fresh_runtime() -> dict:
    sys.path.insert(0, str(BACKEND))
    from app.db import clear_workspace, init_db  # noqa: PLC0415
    from app.llm_runtime import IMAGE_CACHE, RESULT_CACHE  # noqa: PLC0415

    init_db()
    deleted = clear_workspace()
    RESULT_CACHE.clear()
    IMAGE_CACHE.clear()
    # latest-only logs are deleted so a failed iteration cannot accidentally
    # reuse a previous run's JSON as if it belonged to this iteration.
    log_root = ROOT / "data" / "run_logs"
    for path in (
        log_root / "pd_latest.json",
        log_root / "compliance_latest.json",
        log_root / "tasks" / "analysis" / "latest.json",
        log_root / "tasks" / "triangulated" / "latest.json",
    ):
        path.unlink(missing_ok=True)
    return deleted


def wait_health(base_url: str, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"backend exited with code {process.returncode}")
        try:
            if httpx.get(base_url + "/health", timeout=2.0).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise TimeoutError("backend health timeout")


def start_backend(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    # Blind evaluation must exercise the current prompt/model behavior.
    env["NADZOR_LLM_CACHE"] = "0"
    env["NADZOR_BLIND_BENCHMARK"] = "1"
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port),
    ]
    print("+", " ".join(cmd), flush=True)
    return subprocess.Popen(cmd, cwd=BACKEND, env=env)


def upload(base_url: str, path: Path, side: str) -> int:
    with path.open("rb") as handle:
        response = httpx.post(
            base_url + "/documents",
            params={"side": side},
            files={"file": (path.name, handle, "application/pdf")},
            timeout=120.0,
        )
    response.raise_for_status()
    return int(response.json()["id"])


def wait_documents(base_url: str, ids: set[int], timeout: float = 300.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = httpx.get(base_url + "/documents", timeout=20.0)
        response.raise_for_status()
        rows = [row for row in response.json() if int(row["id"]) in ids]
        if len(rows) == len(ids) and all(row.get("status") != "parsing" for row in rows):
            bad = [row for row in rows if row.get("status") != "ok"]
            if bad:
                raise RuntimeError("document parsing failed: " + json.dumps(bad, ensure_ascii=False))
            return rows
        time.sleep(1.0)
    raise TimeoutError("document parsing timeout")


def run_analysis(base_url: str, before_ids: list[int], after_ids: list[int], timeout: float) -> dict:
    response = httpx.post(
        base_url + "/triangulated-runs",
        json={"before_document_ids": before_ids, "after_document_ids": after_ids, "room_keys": []},
        timeout=30.0,
    )
    response.raise_for_status()
    run_id = int(response.json()["id"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = httpx.get(base_url + f"/triangulated-runs/{run_id}", timeout=30.0)
        current.raise_for_status()
        data = current.json()
        if data.get("status") in {"done", "error", "cancelled"}:
            if data.get("status") != "done":
                raise RuntimeError(f"analysis {run_id} ended as {data.get('status')}: {data.get('error')}")
            return data
        time.sleep(1.0)
    raise TimeoutError(f"analysis {run_id} timeout")


def copy_if_exists(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def run_peer_review(iter_dir: Path) -> None:
    completed = run([sys.executable, "scripts/gigachat_current_review.py"], cwd=ROOT, check=False)
    if completed.returncode != 0:
        (iter_dir / "peer_review_error.txt").write_text(
            f"gigachat_current_review.py exited {completed.returncode}\n", encoding="utf-8"
        )
    copy_if_exists(PEER_REVIEW, iter_dir / "gigachat_peer_review_latest.json")


def run_hook(command: str, iter_dir: Path, *, label: str) -> int:
    env = os.environ.copy()
    env["NADZOR_ITERATION_DIR"] = str(iter_dir)
    env["NADZOR_REPO_ROOT"] = str(REPO)
    print(f"+ {label}: {command}", flush=True)
    return subprocess.run(command, cwd=REPO, env=env, shell=True).returncode


def iteration(args, index: int) -> tuple[Path, int | None]:
    deleted = prepare_fresh_runtime()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = git_sha()
    iter_dir = ITERATIONS / f"{index:02d}_{stamp}_{sha[:8]}"
    iter_dir.mkdir(parents=True, exist_ok=True)
    (iter_dir / "manifest.json").write_text(
        json.dumps({
            "iteration": index,
            "git_sha": sha,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "blind": True,
            "llm_result_cache": "disabled_and_cleared",
            "workspace_deleted": deleted,
            "pd": str(args.pd),
            "rd": [str(x) for x in args.rd],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    process = start_backend(args.port)
    base_url = f"http://127.0.0.1:{args.port}"
    try:
        wait_health(base_url, process)
        before_ids = [upload(base_url, args.pd, "before")]
        after_ids = [upload(base_url, path, "after") for path in args.rd]
        wait_documents(base_url, set(before_ids + after_ids), args.document_timeout)
        result = run_analysis(base_url, before_ids, after_ids, args.analysis_timeout)
        (iter_dir / "api_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    copy_if_exists(RUN_LOG, iter_dir / "triangulated_latest.json")
    run_peer_review(iter_dir)

    evaluator_code = None
    if args.evaluator_cmd:
        evaluator_code = run_hook(args.evaluator_cmd, iter_dir, label="evaluator")
        (iter_dir / "evaluator_exit_code.txt").write_text(str(evaluator_code), encoding="utf-8")
    return iter_dir, evaluator_code


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded blind NADZOR.AI iteration loop")
    parser.add_argument("--pd", type=Path, required=True)
    parser.add_argument("--rd", type=Path, action="append", required=True)
    parser.add_argument("--branch", default="claude/new-session-d44es2")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--analysis-timeout", type=float, default=1800.0)
    parser.add_argument("--document-timeout", type=float, default=300.0)
    parser.add_argument("--evaluator-cmd", default="", help="external scorer; exit 0 means success")
    parser.add_argument("--agent-cmd", default="", help="optional coding-agent hook after failed score")
    parser.add_argument("--skip-git-sync", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.pd = args.pd.resolve()
    args.rd = [path.resolve() for path in args.rd]
    for path in [args.pd, *args.rd]:
        if not path.is_file():
            raise SystemExit(f"file not found: {path}")
    if args.max_iterations < 1 or args.max_iterations > 50:
        raise SystemExit("--max-iterations must be 1..50")
    if not args.skip_git_sync:
        sync_git(args.branch)

    for index in range(1, args.max_iterations + 1):
        iter_dir, score = iteration(args, index)
        print(f"iteration artifacts: {iter_dir}")
        if args.evaluator_cmd and score == 0:
            print("external evaluator reports success; stopping")
            return 0
        if index == args.max_iterations:
            break
        if not args.agent_cmd:
            print("no --agent-cmd configured; cannot modify code automatically, stopping")
            break
        before = git_sha()
        code = run_hook(args.agent_cmd, iter_dir, label="coding agent")
        if code != 0:
            print(f"coding agent failed with exit code {code}")
            return code
        if not git_clean():
            print("coding agent left uncommitted changes; stopping rather than auto-committing unknown edits")
            return 4
        after = git_sha()
        if after == before:
            print("coding agent produced no commit; stopping")
            return 5

    return 1 if args.evaluator_cmd else 0


if __name__ == "__main__":
    raise SystemExit(main())
