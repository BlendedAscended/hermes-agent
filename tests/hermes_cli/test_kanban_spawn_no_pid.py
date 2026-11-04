"""A spawn that returns no pid must not leave the card `running`.

Measured twice on 2026-11-03 (t_8654c5bb, t_0e4c83ae): the dispatcher claimed a
card, the spawn callable returned None, and the tick reported it as spawned. The
card then sat `running` with a NULL worker_pid until its 15 minute claim expired,
because ``detect_crashed_workers`` filters on ``worker_pid IS NOT NULL`` and
therefore never saw it. No process, no log file, one lost slot.

The fix books that outcome as a spawn failure with ``infrastructure=True``, so the
claim is released immediately, the retry budget is not spent, and the next tick can
try again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def test_spawn_returning_no_pid_releases_the_claim(kanban_home, all_assignees_spawnable):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="ghost card", assignee="alice")

        res = kbd.dispatch_once(conn, dry_run=False, spawn_fn=lambda _t, _w: None)

        task = kb.get_task(conn, tid)
        runs = kb.list_runs(conn, tid)
        events = kb.list_events(conn, tid)

    # Not reported as a spawn, and not left holding the card.
    assert res.spawned == []
    assert task is not None
    assert task.status == "ready"
    assert task.claim_lock is None
    assert task.claim_expires is None
    assert task.worker_pid is None

    # The attempt is on the record, named for what happened.
    assert runs, "the run must be closed, not left open"
    assert runs[0].status == "spawn_failed"
    assert "no pid" in (runs[0].error or "")
    assert any(e.kind == "spawn_failed" for e in events)

    # Infrastructure deferral must not spend the card's retry budget.
    assert (task.consecutive_failures or 0) == 0
