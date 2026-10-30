"""A timed out attempt preserves the handoff and cannot close its successor's run."""

from __future__ import annotations

import hashlib
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_task_continuity import latest_checkpoint


@pytest.mark.platforms("posix")
def test_default_timeout_preserves_checkpoint_for_fenced_successor(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (home / "config.yaml").write_text(
        "kanban:\n  auto_decompose: false\n  default_max_runtime_seconds: 1\n"
    )
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    workspace = tmp_path / "recovery-workspace"
    workspace.mkdir()
    artifact = workspace / "result.json"
    artifact.write_text('{"accepted": true, "fixture": "checkpoint recovery"}\n')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()

    # Only this disposable child can be signalled by the one-shot dispatcher tick.
    worker = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
    )

    try:
        with kbc.connect() as conn:
            task_id = kb.create_task(
                conn,
                title="Resume the saved output verification",
                assignee="default",
                workspace_kind="dir",
                workspace_path=str(workspace),
            )
            reviewer_id = kb.create_task(
                conn,
                title="Review recovered output",
                assignee="default",
                parents=[task_id],
            )
            claimed = kb.claim_task(conn, task_id)
            assert claimed is not None and claimed.max_runtime_seconds is None
            old_run_id = claimed.current_run_id
            kbd._set_worker_pid(conn, task_id, worker.pid)
            checkpoint = {
                "task_id": task_id,
                "run_id": old_run_id,
                "completed_actions": ["write_result"],
                "completed_steps": ["write_result"],
                "artifact": str(artifact),
                "sha256": digest,
                "next_action": "verify_output_digest_then_complete",
            }
            assert kbd.heartbeat_worker(
                conn, task_id, checkpoint=checkpoint,
                expected_run_id=old_run_id,
            )
            old_started = int(time.time()) - 30
            with kb.write_txn(conn):
                conn.execute(
                    "UPDATE task_runs SET started_at = ? WHERE id = ?",
                    (old_started, old_run_id),
                )
            assert kbd._default_max_runtime_seconds() == 1
            dispatch_result = kbd.dispatch_once(conn, max_spawn=0)
            assert dispatch_result.timed_out == [task_id]
            assert dispatch_result.spawned == []
            worker.wait(timeout=5)
            assert worker.returncode == -signal.SIGTERM
            assert kb.get_task(conn, task_id).status == "ready"
            assert kb.get_task(conn, task_id).consecutive_failures == 1
            assert kb.get_task(conn, reviewer_id).status == "todo"

        # Reopen the durable board as a successor would after dispatcher restart.
        with kbc.connect() as conn:
            successor = kb.claim_task(conn, task_id)
            assert successor is not None
            new_run_id = successor.current_run_id
            assert new_run_id != old_run_id
            context = kb.build_worker_context(conn, task_id)
            assert "timed_out" in context
            assert str(artifact) in context
            assert "resume_saved_progress" in context
            assert digest in context
            saved = latest_checkpoint(conn, task_id)
            assert saved["completed_steps"] == ["write_result"]
            assert saved["next_action"] == "resume_saved_progress"
            assert saved["recovery_trigger"] == "next_dispatch_tick_after_timeout"
            assert saved["last_error"].startswith("elapsed ")
            assert hashlib.sha256(Path(saved["artifact"]).read_bytes()).hexdigest() == saved["sha256"]
            heartbeat = next(event for event in kb.list_events(conn, task_id) if event.kind == "heartbeat")
            assert heartbeat.run_id == old_run_id

            assert kb.complete_task(
                conn, task_id, summary="stale predecessor", expected_run_id=old_run_id,
            ) is False
            assert kb.get_task(conn, task_id).current_run_id == new_run_id
            assert kb.get_task(conn, task_id).status == "running"
            metadata = {"artifacts": [saved["artifact"]], "sha256": saved["sha256"], "checkpoint_run_id": old_run_id}
            assert kb.complete_task(
                conn, task_id, summary="Saved output verified; implementation ready for review",
                metadata=metadata, expected_run_id=new_run_id,
            ) is True
            assert kb.get_task(conn, task_id).status == "done"
            assert kb.get_task(conn, task_id).consecutive_failures == 0
            assert kb.get_task(conn, reviewer_id).status == "ready"
            runs = kb.list_runs(conn, task_id)
            assert [(run.id, run.outcome) for run in runs] == [
                (old_run_id, "timed_out"), (new_run_id, "completed"),
            ]
            assert runs[-1].metadata["sha256"] == digest
            completed = [event for event in kb.list_events(conn, task_id) if event.kind == "completed"]
            assert [event.run_id for event in completed] == [new_run_id]
            review_context = kb.build_worker_context(conn, reviewer_id)
            assert "Saved output verified" in review_context and digest in review_context
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=5)
        if worker.stdin is not None:
            worker.stdin.close()
