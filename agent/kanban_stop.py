"""Turn-end guard for kanban workers, which must end with a terminal board tool that hands
the card to whoever owns it next (``kanban_complete``, ``kanban_block``,
``kanban_request_review``, ``kanban_request_changes``). Some models narrate the next step
and stop with no tool calls; Hermes treats that as a clean exit → ``rc=0`` → dispatcher
``protocol_violation``. Policy-only: return a bounded synthetic nudge so the loop continues
instead of exiting.
"""

from __future__ import annotations

from contextlib import closing
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Optional

from agent.delegation_context import owned_kanban_task


# Every tool that ends this worker's responsibility for the card, not just the two that
# close it out: ``kanban_request_review`` moves it to ``review`` (goals.py's continuation /
# finalize prompts tell builders to call it) and ``kanban_request_changes`` returns it to
# ``ready`` (the sdlc-review skill tells reviewers to). Nudging after either asks a worker
# that did the right thing to ``kanban_complete`` a card it must not close.
_TERMINAL_KANBAN_TOOLS = frozenset({
    "kanban_complete",
    "kanban_block",
    "kanban_schedule",
    "kanban_request_review",
    "kanban_request_changes",
})

_DEFAULT_MAX_ATTEMPTS = 2
logger = logging.getLogger(__name__)


def kanban_stop_nudge_enabled() -> bool:
    """On when ``HERMES_KANBAN_TASK`` is set for the dispatcher-owned worker, unless
    ``HERMES_KANBAN_STOP_NUDGE`` disables it. In-process delegate_task children and cron runs
    inherit the env var but own no board task and carry no kanban toolset."""
    if (os.environ.get("HERMES_KANBAN_STOP_NUDGE") or "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return bool(owned_kanban_task())


def _tool_result_payload(msg: dict) -> Optional[dict]:
    """Parsed kanban tool result, or ``None`` when it is not a JSON object.

    Kanban handlers return ``json.dumps({"ok": True, ...})`` on success and
    ``json.dumps({"error": ...})`` on every rejection (``tools/registry.py``).
    A tool call alone is not proof: the call may have been rejected for
    unsatisfied parents, phantom ``created_cards``, a stale run, or a goal
    judge veto, leaving the card ``running``.
    """
    content = msg.get("content")
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _call_identity(call: Any) -> tuple[str, str]:
    """Retain SDK object support as well as persisted dict tool calls."""
    if isinstance(call, dict):
        function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else call.get("name")
        return str(call.get("id") or ""), str(name or "")
    function = getattr(call, "function", None)
    name = getattr(function, "name", "") if function is not None else getattr(call, "name", "")
    return str(getattr(call, "id", "") or ""), str(name or "")


def session_called_kanban_terminal(
    messages: Iterable[dict] | None, task_id: Optional[str] = None,
    *, run_id: Optional[str] = None,
) -> bool:
    """Require a paired successful lifecycle result for the exact owned task/run."""
    calls: dict[str, str] = {}
    for msg in filter(lambda m: isinstance(m, dict), messages or ()):
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or ():
                call_id, name = _call_identity(call)
                if call_id:
                    calls[call_id] = name
            continue
        if msg.get("role") != "tool":
            continue
        name = calls.pop(str(msg.get("tool_call_id") or ""), "")
        if name not in _TERMINAL_KANBAN_TOOLS:
            continue
        result_name = msg.get("name") or msg.get("tool_name")
        if result_name and result_name != name:
            continue
        payload = _tool_result_payload(msg)
        if payload is None or payload.get("ok") is not True or payload.get("error"):
            continue
        acknowledged = payload.get("task_id")
        if not acknowledged or (task_id and str(acknowledged) != task_id):
            continue
        if run_id and str(payload.get("run_id")) != run_id:
            continue
        return True
    return False


def _pinned_run_released(task_id: str) -> Optional[bool]:
    """Read only the pinned run: True released, False still owned, None unknown.

    Lost transcript acknowledgments must not restart a settled or superseded
    worker. Missing pins or failed reads do not prove a successful handoff.
    """
    db_path = (os.environ.get("HERMES_KANBAN_DB") or "").strip()
    run_id = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
    claim_lock = (os.environ.get("HERMES_KANBAN_CLAIM_LOCK") or "").strip()
    if not all((db_path, run_id, claim_lock)):
        return None
    try:
        uri = f"{Path(db_path).absolute().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT t.status, t.current_run_id, t.claim_lock, t.claim_expires, "
                "t.worker_pid, r.id AS run_id, r.outcome, r.ended_at, "
                "r.claim_expires AS run_expires, r.worker_pid AS run_pid "
                "FROM tasks t LEFT JOIN task_runs r ON r.id = ? AND r.task_id = t.id "
                "WHERE t.id = ?", (int(run_id), task_id),
            ).fetchone()
        if row is None or row["run_id"] is None:
            return None
        if (row["status"] != "running" or str(row["current_run_id"]) != run_id
                or row["ended_at"] is not None or row["outcome"] is not None
                or row["claim_lock"] != claim_lock):
            return True
        expiries = (row["claim_expires"], row["run_expires"])
        if any(expiry is None for expiry in expiries):
            return None
        if min(int(expiry) for expiry in expiries) < int(time.time()):
            return True
        pids = (row["worker_pid"], row["run_pid"])
        return any(pid is not None and int(pid) != os.getpid() for pid in pids)
    except (sqlite3.Error, OSError, ValueError):
        logger.warning("kanban stop guard could not verify pinned run ownership", exc_info=True)
        return None


def build_kanban_stop_nudge(
    *,
    messages: Iterable[dict] | None = None,
    attempts: int = 0,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    task_id: Optional[str] = None,
    step_verdict: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Synthetic follow-up when a kanban worker exits without a terminal tool; ``None`` when
    the guard should not fire (not a kanban worker, already completed/blocked, budget exhausted)."""
    owned_tid = (task_id or os.environ.get("HERMES_KANBAN_TASK") or "").strip()
    if (
        not kanban_stop_nudge_enabled()
        or attempts >= max_attempts
    ):
        return None

    released = _pinned_run_released(owned_tid)
    if released is True:
        return None
    run_id = (os.environ.get("HERMES_KANBAN_RUN_ID") or "").strip()
    if released is None and session_called_kanban_terminal(
        messages, owned_tid or None, run_id=run_id or None,
    ):
        return None

    tid = owned_tid or "this task"
    jev_note = ""
    if isinstance(step_verdict, dict) and step_verdict.get("action") in ("needs_human", "stop_success"):
        jev_action = step_verdict["action"]
        jev_reason = str(step_verdict.get("reason") or "")
        if jev_action == "needs_human":
            jev_note = (
                f"\n\nJev step evaluator flagged `needs_human` ({jev_reason}). "
                "Call `kanban_block(reason=...)` instead of stopping with plain text."
            )
        else:
            jev_note = (
                f"\n\nJev step evaluator observed `stop_success` ({jev_reason}). "
                "Call `kanban_complete(summary=..., artifacts=[...])` or "
                "`kanban_request_review(summary=...)` instead of stopping with plain text."
            )
    return (
        "[System: You are a Hermes kanban worker. A plain-text reply is NOT a "
        "terminal state for the board.\n\n"
        f"Task `{tid}` has not been handed off: this session has no successful "
        "terminal board call (`kanban_complete` / `kanban_request_review` / "
        "`kanban_block`). A call that returned an error did not move the card; if "
        "your last handoff was rejected, fix what the error named and retry it. "
        "Ending now causes a protocol violation (clean exit with the card still "
        "`running`).\n\n"
        "Check `kanban_show` before retrying: if this run no longer owns the card, "
        "stop without changing the successor's task. Otherwise, do this in your next "
        "response instead of narrating intent:\n"
        "1. Finish any remaining deliverable (write the required file(s) now).\n"
        "2. Call `kanban_complete(summary=..., artifacts=[...])` if the work is done "
        "and needs no review, `kanban_request_review(summary=...)` if it is a code "
        "change that needs same-card review, OR `kanban_block(reason=...)` if you are "
        "blocked. Reviewers approve with `kanban_complete` or send the card back with "
        "`kanban_request_changes(reason=...)`.\n\n"
        f"Never end a turn with only a promise of future action. Repeated "
        f"protocol violations will block this task and require manual intervention.{jev_note}]"
    )


__all__ = ["build_kanban_stop_nudge", "kanban_stop_nudge_enabled", "session_called_kanban_terminal"]
