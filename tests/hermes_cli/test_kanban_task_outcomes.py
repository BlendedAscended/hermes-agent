from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli.kanban_task_continuity import (
    OutcomeReceiptError,
    accept_outcome,
    accept_and_reconcile,
    latest_checkpoint,
    reconcile_pending_outcomes,
    task_input_digest,
)


@pytest.fixture
def continuity_board(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    with kbc.connect_closing() as conn:
        yield conn


def _proof(tmp_path, conn, task, run_id, *, disposition="complete", tenant="acct-a"):
    report = tmp_path / f"result-{task.id}-{run_id}.json"
    report.write_text(json.dumps({"command": "python -m pytest", "exit_code": 0,
        "passed": 3, "failed": 0, "skipped": 1}, sort_keys=True))
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    receipt = {
        "version": 1, "task_id": task.id, "run_id": run_id, "tenant": tenant,
        "input_sha256": task_input_digest(conn, task.id),
        "idempotency_key": f"{task.id}:{run_id}:proof-1", "disposition": disposition,
        "external_effect_state": "none", "completed_steps": ["implementation", "verification"],
        "verification": {"exit_code": 0, "passed": 3, "failed": 0, "skipped": 1,
            "evidence_path": str(report), "evidence_sha256": digest},
        "evidence": [{"path": str(report), "sha256": digest}],
    }
    handoff = {"summary": "Verified task evidence", "result": None,
        "metadata": {"tests_run": ["python -m pytest"]}, "created_cards": []}
    token = task.claim_lock
    return token, receipt, handoff, report


def test_heartbeat_checkpoint_and_lost_terminal_write_reconcile_before_spawn(continuity_board, tmp_path):
    conn = continuity_board
    task_id = kb.create_task(conn, title="Persist verified work", body="finish one scoped change",
        assignee="default", tenant="acct-a")
    claimed = kb.claim_task(conn, task_id, claimer="fixture-host:worker-1")
    assert claimed and claimed.current_run_id
    run_id = claimed.current_run_id
    checkpoint = {"completed_steps": ["write"], "next_action": "run_focused_tests",
        "evidence": [], "waiting_reason": None, "pending_dependency": None,
        "recovery_owner": "kanban_dispatcher", "recovery_trigger": "next_tick", "last_error": None}
    assert kbd.heartbeat_worker(conn, task_id, checkpoint=checkpoint, expected_run_id=run_id)
    saved_checkpoint = latest_checkpoint(conn, task_id)
    assert saved_checkpoint["next_action"] == "run_focused_tests"
    assert saved_checkpoint["run_id"] == run_id
    assert task_input_digest(conn, task_id) in kb.build_worker_context(conn, task_id)

    token, receipt, handoff, report = _proof(tmp_path, conn, claimed, run_id)
    receipt_id, duplicate = accept_outcome(conn, task_id, run_id, token, receipt, handoff)
    assert not duplicate
    assert kb.get_task(conn, task_id).status == "running"  # simulate crash before terminal write
    assert report.exists()

    # A fresh dispatcher tick consumes the accepted receipt before any spawn.
    kbd.dispatch_once(conn, spawn_fn=lambda *_args, **_kwargs: pytest.fail("must not respawn accepted work"),
        max_spawn=1)
    assert kb.get_task(conn, task_id).status == "done"
    row = conn.execute("SELECT state,reconciled_at FROM task_outcome_receipts WHERE id=?", (receipt_id,)).fetchone()
    assert row["state"] == "reconciled" and row["reconciled_at"]
    event_count = conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0]

    # The lost acknowledgement can be retried after artifacts or the worker disappear.
    report.unlink()
    replay_id, duplicate = accept_outcome(conn, task_id, run_id, token, receipt, handoff)
    assert replay_id == receipt_id and duplicate
    assert kb.get_task(conn, task_id).status == "done"
    assert conn.execute("SELECT COUNT(*) FROM task_events WHERE task_id=?", (task_id,)).fetchone()[0] == event_count


def test_rejects_foreign_tenant_stale_run_conflict_and_ambiguous_effect(continuity_board, tmp_path):
    conn = continuity_board
    task_id = kb.create_task(conn, title="Keep tenant proof isolated", assignee="default", tenant="acct-a")
    first = kb.claim_task(conn, task_id, claimer="fixture-host:first")
    token, receipt, handoff, _ = _proof(tmp_path, conn, first, first.current_run_id)
    foreign = dict(receipt, tenant="acct-b")
    with pytest.raises(OutcomeReceiptError, match="tenant"):
        accept_outcome(conn, task_id, first.current_run_id, token, foreign, handoff)
    ambiguous = dict(receipt, external_effect_state="unknown")
    with pytest.raises(OutcomeReceiptError, match="ambiguous"):
        accept_outcome(conn, task_id, first.current_run_id, token, ambiguous, handoff)
    assert kb.get_task(conn, task_id).status == "running"

    # Simulate a timeout/reclaim and an explicitly authorized successor claim.
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready',claim_lock=NULL,claim_expires=NULL WHERE id=?", (task_id,))
        conn.execute("UPDATE task_runs SET status='timed_out',outcome='timed_out',ended_at=1 WHERE id=?",
            (first.current_run_id,))
    successor = kb.claim_task(conn, task_id, claimer="fixture-host:successor")
    assert successor and successor.current_run_id != first.current_run_id
    stale = dict(receipt, run_id=first.current_run_id + 999)
    with pytest.raises(OutcomeReceiptError, match="run_id"):
        accept_outcome(conn, task_id, successor.current_run_id, successor.claim_lock, stale, handoff)

    # A successor may reconcile accepted evidence from the previous run, but the
    # predecessor cannot create a new mutation receipt after the claim changed.
    receipt_id, _ = accept_outcome(conn, task_id, successor.current_run_id, successor.claim_lock,
        dict(receipt, run_id=successor.current_run_id,
             idempotency_key=f"{task_id}:{successor.current_run_id}:new-proof"), handoff)
    assert receipt_id > 0


def test_review_receipt_uses_canonical_same_card_review_transition(continuity_board, tmp_path):
    conn = continuity_board
    task_id = kb.create_task(conn, title="Review this verified work", assignee="default", tenant="acct-a")
    claimed = kb.claim_task(conn, task_id, claimer="fixture-host:reviewer")
    token, receipt, handoff, _ = _proof(tmp_path, conn, claimed, claimed.current_run_id, disposition="review")
    receipt_id, _ = accept_outcome(conn, task_id, claimed.current_run_id, token, receipt, handoff)
    assert reconcile_pending_outcomes(conn) == [receipt_id]
    assert kb.get_task(conn, task_id).status == "review"
    assert reconcile_pending_outcomes(conn) == []  # reconciled receipts never transition twice


def test_authorized_dispatcher_consumes_prior_receipt_after_successor_claim(continuity_board, tmp_path):
    conn = continuity_board
    task_id = kb.create_task(conn, title="Recover an accepted prior attempt", assignee="default", tenant="acct-a")
    predecessor = kb.claim_task(conn, task_id, claimer="fixture-host:predecessor")
    old_token, receipt, handoff, _ = _proof(tmp_path, conn, predecessor, predecessor.current_run_id)
    receipt_id, _ = accept_outcome(conn, task_id, predecessor.current_run_id, old_token, receipt, handoff)
    with kb.write_txn(conn):
        conn.execute("UPDATE tasks SET status='ready',claim_lock=NULL,claim_expires=NULL,current_run_id=NULL WHERE id=?",
            (task_id,))
        conn.execute("UPDATE task_runs SET status='timed_out',outcome='timed_out',ended_at=1 WHERE id=?",
            (predecessor.current_run_id,))
    successor = kb.claim_task(conn, task_id, claimer="fixture-host:successor")
    assert successor and successor.current_run_id != predecessor.current_run_id

    with pytest.raises(OutcomeReceiptError, match="not the active run"):
        accept_and_reconcile(conn, task_id, predecessor.current_run_id, old_token, receipt, handoff)
    assert kb.get_task(conn, task_id).status == "running"
    assert reconcile_pending_outcomes(conn) == [receipt_id]
    assert kb.get_task(conn, task_id).status == "done"
    assert kb.complete_task(conn, task_id, summary="stale owner", expected_run_id=predecessor.current_run_id) is False
