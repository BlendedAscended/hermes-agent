"""Heartbeat writes and durable worker progress checkpoints."""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_task_continuity as continuity


def heartbeat_worker(conn: sqlite3.Connection, task_id: str, *, note: Optional[str] = None,
                     checkpoint: Optional[dict] = None,
                     expected_run_id: Optional[int] = None) -> bool:
    now = int(time.time())
    with kb.write_txn(conn):
        sql = "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ? AND status = 'running'"
        params: tuple = (now, task_id)
        if expected_run_id is not None:
            sql += " AND current_run_id = ?"
            params += (int(expected_run_id),)
        if conn.execute(sql, params).rowcount != 1:
            return False
        run_id = int(expected_run_id) if expected_run_id is not None else kb._current_run_id(conn, task_id)
        if run_id is not None:
            conn.execute("UPDATE task_runs SET last_heartbeat_at = ? WHERE id = ?", (now, run_id))
            task = kb.get_task(conn, task_id)
            if task is None or not task.claim_lock:
                return False
            continuity.checkpoint_worker(conn, task_id, run_id, task.claim_lock, checkpoint, note=note)
        kb._append_event(conn, task_id, "heartbeat", {"note": note} if note else None, run_id=run_id)
    return True
