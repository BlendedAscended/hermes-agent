"""Tests for the kanban worker turn-end stop guard."""

from __future__ import annotations

import json

import pytest

from agent.kanban_stop import (
    build_kanban_stop_nudge,
    kanban_stop_nudge_enabled,
    session_called_kanban_terminal,
)


@pytest.fixture
def clear_kanban_env(monkeypatch):
    for var in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_STOP_NUDGE", "HERMES_KANBAN_DB",
                "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_env_can_disable(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    clear_kanban_env.setenv("HERMES_KANBAN_STOP_NUDGE", "0")
    assert kanban_stop_nudge_enabled() is False
    assert build_kanban_stop_nudge(messages=[]) is None


def test_nudge_disabled_inside_delegated_child(clear_kanban_env):
    from agent.delegation_context import delegated_child_context

    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_parent")

    assert kanban_stop_nudge_enabled() is True
    with delegated_child_context():
        assert kanban_stop_nudge_enabled() is False
        assert build_kanban_stop_nudge(messages=[]) is None
    assert kanban_stop_nudge_enabled() is True


def test_nudge_disabled_inside_non_dispatcher_context(clear_kanban_env):
    from agent.delegation_context import non_dispatcher_owned_context

    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_parent")

    assert kanban_stop_nudge_enabled() is True
    with non_dispatcher_owned_context():
        assert kanban_stop_nudge_enabled() is False
        assert build_kanban_stop_nudge(messages=[]) is None
    assert kanban_stop_nudge_enabled() is True


def test_nudge_when_no_terminal_tool(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_46be8aa5")
    messages = [
        {"role": "user", "content": "work kanban task"},
        {
            "role": "assistant",
            "content": "Let me write the comprehensive recipe.",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "kanban_heartbeat", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "kanban_heartbeat", "tool_call_id": "1", "content": "ok"},
    ]
    nudge = build_kanban_stop_nudge(messages=messages, attempts=0)
    assert nudge is not None
    assert "kanban_complete" in nudge
    assert "kanban_block" in nudge
    assert "t_46be8aa5" in nudge


def test_no_nudge_after_kanban_complete(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "kanban_complete", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "kanban_complete", "tool_call_id": "1", "content": json.dumps({"ok": True, "task_id": "t_abc", "run_id": 1})},
    ]
    assert session_called_kanban_terminal(messages) is True
    assert build_kanban_stop_nudge(messages=messages) is None


# ── Integration: agent nudge + dispatcher bounded retry ──────────────
# These tests verify the two layers compose correctly: the agent-side
# nudge fires first (up to 2 attempts), and if the worker still exits
# without a terminal call, the dispatcher's bounded retry (streak of 3)
# handles it.  See also tests/hermes_cli/test_kanban_core_functionality.py
# for the dispatcher-side streak tests.


@pytest.mark.parametrize(
    "tool_name,who",
    [
        ("kanban_request_review", "build worker handing off for same-card review"),
        ("kanban_request_changes", "review agent sending the card back"),
        ("kanban_schedule", "worker parking the card on a timed wait"),
    ],
)
def test_no_nudge_after_handoff_tool(clear_kanban_env, tool_name, who):
    """Handoff tools end the worker's turn just like complete/block.

    Both move the card out of ``running``, and the worker is told to call
    them — goals.py's continuation/finalize prompts name
    ``kanban_request_review``; the force-loaded sdlc-review skill names
    ``kanban_request_changes``. Nudging afterwards asks a worker that did
    the right thing to close a card it must not close.
    """
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_handoff")
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": tool_name, "tool_call_id": "1", "content": json.dumps({"ok": True, "task_id": "t_handoff", "run_id": 1, "status": tool_name})},
    ]
    assert session_called_kanban_terminal(messages) is True, who
    assert build_kanban_stop_nudge(messages=messages) is None


def test_nudge_still_fires_for_non_terminal_kanban_tool(clear_kanban_env):
    """Widening the set must not swallow the case the guard exists for."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_abc")
    messages = [
        {
            "role": "assistant",
            "content": "Let me open the review next.",
            "tool_calls": [
                {
                    "id": "1",
                    "type": "function",
                    "function": {"name": "kanban_comment", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "name": "kanban_comment", "tool_call_id": "1", "content": "ok"},
    ]
    assert session_called_kanban_terminal(messages) is False
    nudge = build_kanban_stop_nudge(messages=messages)
    assert nudge is not None
    # The nudge offers every worker exit, not just close-out; a card that must go
    # through review must never be steered to ``kanban_complete`` alone.
    assert "kanban_request_review" in nudge and "kanban_block" in nudge


def _terminal_call(name: str, call_id: str = "1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def test_rejected_complete_is_not_a_handoff(clear_kanban_env):
    """A refused completion leaves the card running; the guard must still fire."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_rejected")
    rejection = {
        "error": "kanban_complete blocked: the following created_cards do not exist: "
        "t_phantom. Your task is still in-flight (no state change)."
    }
    messages = [
        _terminal_call("kanban_complete"),
        {
            "role": "tool",
            "name": "kanban_complete",
            "tool_call_id": "1",
            "content": json.dumps(rejection),
        },
    ]
    assert session_called_kanban_terminal(messages) is False
    nudge = build_kanban_stop_nudge(messages=messages)
    assert nudge is not None
    assert "returned an error" in nudge and "retry" in nudge


def test_terminal_call_without_result_is_not_a_handoff(clear_kanban_env):
    """The turn ended during the call; nothing proves the transition landed."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_pending")
    messages = [_terminal_call("kanban_complete")]
    assert session_called_kanban_terminal(messages) is False
    assert build_kanban_stop_nudge(messages=messages) is not None


def test_unknown_result_text_is_not_a_handoff(clear_kanban_env):
    """Only the success envelope counts; ad-hoc text is unverifiable."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_unknown")
    messages = [
        _terminal_call("kanban_complete"),
        {"role": "tool", "name": "kanban_complete", "tool_call_id": "1", "content": "done"},
    ]
    assert session_called_kanban_terminal(messages) is False


def test_mismatched_task_ack_is_not_a_handoff(clear_kanban_env):
    """A success envelope for another card does not prove this card moved."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_owned")
    messages = [
        _terminal_call("kanban_complete"),
        {
            "role": "tool",
            "name": "kanban_complete",
            "tool_call_id": "1",
            "content": json.dumps({"ok": True, "task_id": "t_other"}),
        },
    ]
    assert session_called_kanban_terminal(messages, "t_owned") is False
    assert build_kanban_stop_nudge(messages=messages) is not None


def test_success_after_earlier_rejection_is_a_handoff(clear_kanban_env):
    """The worker corrected the error and retried: the card did move."""
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_retried")
    messages = [
        _terminal_call("kanban_complete", "1"),
        {
            "role": "tool",
            "name": "kanban_complete",
            "tool_call_id": "1",
            "content": json.dumps({"error": "unsatisfied parent dependencies"}),
        },
        _terminal_call("kanban_complete", "2"),
        {
            "role": "tool",
            "name": "kanban_complete",
            "tool_call_id": "2",
            "content": json.dumps({"ok": True, "task_id": "t_retried", "run_id": 1}),
        },
    ]
    assert session_called_kanban_terminal(messages) is True
    assert build_kanban_stop_nudge(messages=messages) is None


@pytest.mark.parametrize("call_id,name,task_id,run_id,has_call", [
    pytest.param("1", "kanban_complete", "t_owned", 12, False, id="orphan"),
    pytest.param("another", "kanban_complete", "t_owned", 12, True, id="wrong_call"),
    pytest.param("1", "kanban_block", "t_owned", 12, True, id="wrong_name"),
    pytest.param("1", "kanban_complete", None, 12, True, id="missing_task"),
    pytest.param("1", "kanban_complete", "t_owned", None, True, id="missing_run"),
    pytest.param("1", "kanban_complete", "t_owned", 11, True, id="wrong_run"),
])
def test_handoff_requires_paired_owned_task_and_run(
    clear_kanban_env, call_id, name, task_id, run_id, has_call,
):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_owned")
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "12")
    payload = {key: value for key, value in {
        "ok": True, "task_id": task_id, "run_id": run_id,
    }.items() if value is not None}
    calls = [_terminal_call("kanban_complete")] if has_call else []
    messages = [*calls, {
        "role": "tool", "name": name, "tool_call_id": call_id,
        "content": json.dumps(payload),
    }]
    assert build_kanban_stop_nudge(messages=messages) is not None


def test_restored_handoff_result_resolves_name_from_call(clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_owned")
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "12")
    messages = [_terminal_call("kanban_complete"), {
        "role": "tool", "tool_call_id": "1",
        "content": json.dumps({"ok": True, "task_id": "t_owned", "run_id": 12}),
    }]
    assert build_kanban_stop_nudge(messages=messages) is None


@pytest.fixture
def pinned_run(tmp_path, clear_kanban_env):
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    from hermes_cli import kanban_db_dispatch as kbd
    import os

    db_path = tmp_path / "kanban.db"
    with kbc.connect(db_path) as conn:
        task_id = kb.create_task(conn, title="Exact run handoff", assignee="worker")
        task = kb.claim_task(conn, task_id)
        kbd._set_worker_pid(conn, task_id, os.getpid())
        clear_kanban_env.setenv("HERMES_KANBAN_TASK", task_id)
        clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", str(task.current_run_id))
        clear_kanban_env.setenv("HERMES_KANBAN_CLAIM_LOCK", task.claim_lock)
        clear_kanban_env.setenv("HERMES_KANBAN_DB", str(db_path))
        yield kb, conn, task_id, task.current_run_id


def test_pinned_board_rejection_keeps_nudge_until_completion_lands(pinned_run):
    kb, conn, task_id, run_id = pinned_run
    messages = [_terminal_call("kanban_complete"), {
        "role": "tool", "name": "kanban_complete", "tool_call_id": "1",
        "content": json.dumps({"error": "completion refused"}),
    }]
    assert build_kanban_stop_nudge(messages=messages) is not None
    assert kb.complete_task(conn, task_id, summary="verified", expected_run_id=run_id)
    # A lost transcript acknowledgment cannot restart a completed run.
    assert build_kanban_stop_nudge(messages=messages) is None


def test_superseded_worker_is_not_nudged_into_successor(pinned_run):
    kb, conn, task_id, run_id = pinned_run
    assert kb.reclaim_task(conn, task_id, signal_fn=lambda *_: None)
    successor = kb.claim_task(conn, task_id)
    assert successor.current_run_id != run_id
    assert build_kanban_stop_nudge(messages=[]) is None
    assert kb.get_task(conn, task_id).current_run_id == successor.current_run_id


def test_unreadable_pinned_board_does_not_count_as_handoff(clear_kanban_env, tmp_path):
    clear_kanban_env.setenv("HERMES_KANBAN_TASK", "t_owned")
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "12")
    clear_kanban_env.setenv("HERMES_KANBAN_CLAIM_LOCK", "worker:claim")
    clear_kanban_env.setenv("HERMES_KANBAN_DB", str(tmp_path / "missing.db"))
    assert build_kanban_stop_nudge(messages=[]) is not None
    assert not (tmp_path / "missing.db").exists()


def test_active_board_cannot_be_overridden_by_success_text(pinned_run):
    _, _, task_id, run_id = pinned_run
    messages = [_terminal_call("kanban_complete"), {
        "role": "tool", "name": "kanban_complete", "tool_call_id": "1",
        "content": json.dumps({"ok": True, "task_id": task_id, "run_id": run_id}),
    }]
    assert build_kanban_stop_nudge(messages=messages) is not None


@pytest.mark.parametrize("expiry,should_nudge", [(-1, False), ("invalid", True)])
def test_expired_or_unverifiable_lease(pinned_run, expiry, should_nudge):
    kb, conn, task_id, _ = pinned_run
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?", (expiry, task_id))
    assert (build_kanban_stop_nudge(messages=[]) is not None) is should_nudge


def test_missing_pinned_run_cannot_be_treated_as_completed(pinned_run, clear_kanban_env):
    clear_kanban_env.setenv("HERMES_KANBAN_RUN_ID", "99999999")
    assert build_kanban_stop_nudge(messages=[]) is not None
