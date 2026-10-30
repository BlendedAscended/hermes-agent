"""A real one-shot dispatcher subprocess owns a disposable board tick."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHILD_CODE = r'''
import hashlib, inspect, json, os, subprocess, sys, time
from pathlib import Path
from hermes_cli import kanban_db_connect as kbc, kanban_db_dispatch as kbd
from hermes_cli.profiles import get_active_profile_name

root, marker, release = map(Path, sys.argv[1:4])
mode = sys.argv[4]
db_path = Path(os.environ["HERMES_KANBAN_DB"]).resolve()
source_path = Path(inspect.getsourcefile(kbd.dispatch_once)).resolve()
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

original_tick = kbd._dispatch_once_locked
def hold_tick(conn, **kwargs):
    marker.write_text(json.dumps({
        "pid": os.getpid(), "executable": sys.executable,
        "revision": revision, "profile": get_active_profile_name(),
        "board": "default", "database": str(db_path),
        "lock_path": str(db_path.with_name(db_path.name + ".dispatch.lock")),
        "dispatcher_source": str(source_path),
        "dispatcher_source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }))
    deadline = time.monotonic() + 20
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("parent did not release disposable dispatch tick")
        time.sleep(0.01)
    return original_tick(conn, **kwargs)

if mode == "hold":
    kbd._dispatch_once_locked = hold_tick
with kbc.connect_closing() as conn:
    result = kbd.dispatch_once(conn, board="default")
print(json.dumps({
    "pid": os.getpid(), "executable": sys.executable,
    "revision": revision, "profile": get_active_profile_name(), "board": "default",
    "skipped_locked": result.skipped_locked,
    "skipped_nonspawnable": result.skipped_nonspawnable,
    "spawned": result.spawned,
}))
'''


def _wait_for(path: Path, proc: subprocess.Popen, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return json.loads(path.read_text())
        if proc.poll() is not None:
            raise AssertionError(f"dispatcher exited before acquiring its tick lock: {proc.returncode}")
        time.sleep(0.02)
    raise AssertionError("dispatcher did not acquire its tick lock before timeout")


def test_disposable_dispatcher_process_owns_tick_and_fences_competing_process(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text(
        "kanban:\n  auto_decompose: false\n  dispatch_in_gateway: false\n"
        "  default_max_runtime_seconds: 5400\n"
    )
    db_path = tmp_path / "boards" / "luna-disposable.db"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_CONFIG", str(config))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))
    monkeypatch.setenv("HERMES_KANBAN_BOARD", "default")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kbc.init_db(db_path)
    task_id = "t_luna_" + uuid.uuid4().hex[:12]
    missing_profile = "ghost-luna-" + uuid.uuid4().hex[:8]
    with kbc.connect(db_path) as conn:
        kb.create_task(conn, title="Disposable dispatcher ownership proof", assignee=missing_profile,
            initial_status="running", workspace_kind="scratch", idempotency_key=task_id)
        actual = conn.execute("SELECT id FROM tasks WHERE idempotency_key = ?", (task_id,)).fetchone()["id"]

    child_env = {
        "HOME": str(Path.home()), "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(PROJECT_ROOT), "PYTHONHASHSEED": "0", "LANG": "C.UTF-8",
        "HERMES_HOME": str(home), "HERMES_CONFIG": str(config),
        "HERMES_KANBAN_DB": str(db_path), "HERMES_KANBAN_BOARD": "default",
        "HERMES_KANBAN_WORKSPACES_ROOT": str(tmp_path / "workspaces"),
    }
    marker, release = tmp_path / "tick-owner.json", tmp_path / "release-tick"
    argv = [sys.executable, "-c", CHILD_CODE, str(PROJECT_ROOT), str(marker), str(release), "hold"]
    owner = subprocess.Popen(argv, cwd=PROJECT_ROOT, env=child_env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        owner_record = _wait_for(marker, owner)
        assert owner_record["pid"] == owner.pid
        assert Path(owner_record["executable"]).resolve() == Path(sys.executable).resolve()
        assert owner_record["profile"] == "default"
        assert owner_record["board"] == "default"
        assert owner_record["database"] == str(db_path.resolve())
        assert Path(owner_record["dispatcher_source"]).is_file()

        competitor = subprocess.run(
            [sys.executable, "-c", CHILD_CODE, str(PROJECT_ROOT), str(tmp_path / "unused"),
             str(tmp_path / "unused-release"), "plain"], cwd=PROJECT_ROOT, env=child_env,
            text=True, capture_output=True, timeout=20, check=True)
        competitor_record = json.loads(competitor.stdout.strip().splitlines()[-1])
        assert competitor_record["skipped_locked"] is True
        assert competitor_record["pid"] != owner.pid
        assert owner.poll() is None
    finally:
        release.touch()

    stdout, stderr = owner.communicate(timeout=20)
    assert owner.returncode == 0, stderr
    owner_result = json.loads(stdout.strip().splitlines()[-1])
    assert owner_result["pid"] == owner.pid
    assert owner_result["skipped_locked"] is False
    assert owner_result["skipped_nonspawnable"] == [actual]
    assert owner_result["spawned"] == []
    with kbc.connect(db_path) as conn:
        task = kb.get_task(conn, actual)
        assert task.status == "ready"
        assert any(event.kind == "skipped_nonspawnable" for event in kb.list_events(conn, actual))

    proof_path = os.environ.get("LUNA_DISPATCHER_EVIDENCE_PATH")
    if proof_path:
        record = {
            "owner": owner_record, "owner_result": owner_result,
            "competitor": competitor_record, "task_id": actual,
            "executable": str(Path(sys.executable).resolve()),
            "source_commit": owner_record["revision"],
            "task_status_after_tick": task.status,
            "verified": True,
        }
        output = Path(proof_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(record, indent=2) + "\n")
