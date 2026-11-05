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
    # Say what the workspace path IS, not only what it is called. `workspace_path`
    # is the worker's working directory, and a scratch card's directory is
    # deliberately repo-less (#106342) — without this line a worker reads the
    # field as "my checkout", finds an empty dir and blocks on a card that was
    # never broken (t_2da84d09 burned three runs on exactly that, 2026-11-04).
    # Assembled here rather than inside `_ctx_header` because it belongs to the
    # worker-facing contract and `kanban_db` is over its file-length cap.
    from hermes_cli import kanban_db_workspace as _kbw
    _note = _kbw.workspace_contract_note(
        task.workspace_kind, task.workspace_path, branch=task.branch_name)
    if _note:
        lines.append(_note)
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
