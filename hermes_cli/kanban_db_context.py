"""Worker context assembly, kept separate from Kanban lifecycle state transitions."""

from __future__ import annotations

import json
import time
import sqlite3

from hermes_cli import kanban_db as kb


def build_worker_context(conn: sqlite3.Connection, task_id: str) -> str:
    task = kb.get_task(conn, task_id)
    if not task:
        raise ValueError(f"unknown task {task_id}")
    now = int(time.time())
    lines: list[str] = []
    kb._ctx_header(lines, task)
    if task.current_run_id is not None:
        lines.append(f"Active run: {task.current_run_id}")
    kb._ctx_attachments(lines, kb.list_attachments(conn, task_id))
    kb._ctx_prior_attempts(lines, conn, task_id, now)
    kb._ctx_parent_results(lines, conn, task_id, now)
    kb._ctx_role_history(lines, conn, task, now)
    kb._ctx_comments(lines, kb.list_comments(conn, task_id), now)
    from hermes_cli.kanban_task_continuity import latest_checkpoint, task_input_digest
    lines.extend(["", f"Task input SHA-256: {task_input_digest(conn, task_id)}"])
    checkpoint = latest_checkpoint(conn, task_id)
    if checkpoint:
        lines.extend(["", "## Latest recovery checkpoint", "```json",
            json.dumps(checkpoint, ensure_ascii=False, sort_keys=True), "```"])
    return "\n".join(lines).rstrip() + "\n"
