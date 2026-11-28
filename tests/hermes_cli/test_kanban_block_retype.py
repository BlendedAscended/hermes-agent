"""A typed blocked card is re-typed in place toward a human hold, never away from one.

Before this, ``block_task`` refused any typed blocked card, so a human hold over a
``transient`` block needed unblock then block. That handed the card back to the pool
for a dispatcher tick and tripped the loop breaker into ``triage``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with an empty kanban DB."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _blocked(conn, kind):
    tid = kb.create_task(conn, title=f"held as {kind}", assignee="a")
    assert kb.block_task(conn, tid, reason="first hold", kind=kind)
    return tid


def _state(conn, tid):
    row = conn.execute(
        "SELECT status, block_kind, block_recurrences FROM tasks WHERE id = ?", (tid,),
    ).fetchone()
    return row["status"], row["block_kind"], row["block_recurrences"]


def _event_count(conn, tid):
    return conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id = ?", (tid,)).fetchone()[0]


def _last_event(conn, tid):
    row = conn.execute(
        "SELECT kind, payload FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,),
    ).fetchone()
    return row["kind"], (json.loads(row["payload"]) if row["payload"] else {})


@pytest.mark.parametrize("new_kind", ["needs_input", "capability"])
def test_transient_hold_escalates_in_place(kanban_home, new_kind):
    with kbc.connect() as conn:
        tid = _blocked(conn, "transient")
        assert kb.block_task(conn, tid, reason="counsel gate", kind=new_kind)
        assert _state(conn, tid) == ("blocked", new_kind, 1)
        kind, payload = _last_event(conn, tid)
        assert kind == "blocked"
        assert payload["retyped_in_place"] is True
        assert payload["previous_kind"] == "transient"
        assert payload["reason"] == "counsel gate"


def test_human_holds_retype_between_each_other(kanban_home):
    with kbc.connect() as conn:
        tid = _blocked(conn, "needs_input")
        assert kb.block_task(conn, tid, reason="needs a tool", kind="capability")
        assert _state(conn, tid) == ("blocked", "capability", 1)
        assert kb.block_task(conn, tid, reason="back to a question", kind="needs_input")
        assert _state(conn, tid) == ("blocked", "needs_input", 1)


@pytest.mark.parametrize("stored, new", [
    ("needs_input", "transient"),
    ("capability", "transient"),
    ("needs_input", "needs_input"),
    ("transient", "transient"),
    ("needs_input", "dependency"),
    ("transient", "dependency"),
])
def test_refused_moves_leave_the_card_untouched(kanban_home, stored, new):
    with kbc.connect() as conn:
        tid = _blocked(conn, stored)
        before, events_before = _state(conn, tid), _event_count(conn, tid)
        assert kb.block_task(conn, tid, reason="try", kind=new) is False
        assert _state(conn, tid) == before
        assert _event_count(conn, tid) == events_before


def test_kindless_call_and_run_owner_are_refused(kanban_home):
    with kbc.connect() as conn:
        tid = _blocked(conn, "transient")
        assert kb.block_task(conn, tid, reason="no kind") is False
        assert kb.block_task(conn, tid, reason="worker", kind="needs_input", expected_run_id=1) is False
        assert _state(conn, tid)[1] == "transient"


def test_live_run_blocks_retype(kanban_home):
    with kbc.connect() as conn:
        tid = _blocked(conn, "transient")
        conn.execute("UPDATE tasks SET current_run_id = 999 WHERE id = ?", (tid,))
        conn.commit()
        assert kb.block_task(conn, tid, reason="live", kind="needs_input") is False
        assert _state(conn, tid)[1] == "transient"


def test_untyped_breaker_park_still_classifies_in_place(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="breaker parked", assignee="a")
        conn.execute("UPDATE tasks SET status = 'blocked', block_kind = NULL WHERE id = ?", (tid,))
        conn.commit()
        assert kb.block_task(conn, tid, reason="classify", kind="needs_input")
        assert _state(conn, tid) == ("blocked", "needs_input", 1)
        assert _last_event(conn, tid)[1]["classified_in_place"] is True


def test_retyped_hold_is_not_promoted_by_recompute_ready(kanban_home):
    with kbc.connect() as conn:
        tid = _blocked(conn, "transient")
        assert kb.block_task(conn, tid, reason="counsel gate", kind="needs_input")
        kb.recompute_ready(conn)
        assert _state(conn, tid)[0] == "blocked"
