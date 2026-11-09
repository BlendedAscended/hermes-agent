"""Task-scoped progress and outcome evidence for dispatcher recovery."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from hermes_cli import kanban_db as kb

# Verification evidence used to live only where the worker happened to write it,
# usually a profile scratch directory that is pruned after a day. The digest in
# the receipt then pointed at nothing, so an old acceptance could never be
# re-checked. Every receipt now keeps its own copy here, named by that digest.
def _evidence_root() -> Path:
    """Resolved per call so an isolated board root (tests, a second profile) stays isolated."""
    override = os.environ.get("HERMES_EVIDENCE_ROOT")
    if override:
        return Path(override).expanduser()
    return kb.kanban_home() / "evidence"


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))[:80] or "unknown"

CONTINUITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_checkpoints (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER NOT NULL,
 tenant TEXT, owner_token TEXT NOT NULL, payload_json TEXT NOT NULL,
 digest TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoint_task ON task_checkpoints(task_id, id);
CREATE TABLE IF NOT EXISTS task_outcome_receipts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER NOT NULL,
 tenant TEXT, owner_token TEXT NOT NULL, input_digest TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, disposition TEXT NOT NULL, payload_json TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'accepted', accepted_at INTEGER NOT NULL,
 reconciled_at INTEGER, transition_run_id INTEGER,
 UNIQUE(task_id, run_id), UNIQUE(task_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_receipt_pending ON task_outcome_receipts(state, task_id);
"""


class OutcomeReceiptError(ValueError):
    """A worker receipt is incomplete, stale, conflicting, or untrusted."""


def validate_handoff_artifacts(handoff: dict) -> None:
    metadata = handoff.get("metadata")
    if not isinstance(metadata, dict):
        return
    artifacts = metadata.get("artifacts") or []
    if not isinstance(artifacts, list):
        raise OutcomeReceiptError("handoff artifacts must be an array")
    for artifact in artifacts:
        if not isinstance(artifact, str) or not Path(artifact).is_absolute() or not Path(artifact).is_file():
            raise OutcomeReceiptError(f"handoff artifact is not a readable absolute file: {artifact!r}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    for statement in CONTINUITY_SCHEMA_SQL.split(";"):
        if statement.strip():
            conn.execute(statement)


def task_input_digest(conn: sqlite3.Connection, task_id: str) -> str:
    row = conn.execute(
        "SELECT title, body, completion_contract, tenant FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise OutcomeReceiptError(f"unknown task {task_id}")
    raw = json.dumps(dict(row), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _json_digest(payload: dict) -> tuple[str, str]:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def _task_run(conn: sqlite3.Connection, task_id: str, run_id: int):
    return conn.execute(
        "SELECT t.status, t.current_run_id, t.claim_lock, t.tenant, r.claim_lock AS run_lock "
        "FROM tasks t JOIN task_runs r ON r.task_id=t.id WHERE t.id=? AND r.id=?",
        (task_id, int(run_id)),
    ).fetchone()


def _norm_tenant(value: Any) -> Any:
    """Normalize the string spellings of an empty tenant to None.

    A task with `tenant IS NULL` must be receipted with a bare JSON null. Some
    models serialize that as the string "null"/"None" instead, which then fails
    the ownership check on a card whose work is already finished. Treat the
    string spellings of "no tenant" as no tenant.
    """
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() in {"null", "none", "nil", "undefined"}:
            return None
    return value


def _verify_owner(conn: sqlite3.Connection, task_id: str, run_id: int, tenant: Any, token: str):
    row = _task_run(conn, task_id, run_id)
    if row is None or row["status"] != "running" or row["current_run_id"] != int(run_id):
        raise OutcomeReceiptError("receipt task/run is not the active run")
    if _norm_tenant(row["tenant"]) != _norm_tenant(tenant):
        raise OutcomeReceiptError("receipt tenant does not match the task")
    if not token or row["claim_lock"] != token or row["run_lock"] != token:
        raise OutcomeReceiptError("receipt claim token is stale or does not own the active run")
    return row


def _persist_evidence(receipt: dict, task_id: str) -> None:
    """Copy the verification artifact somewhere that outlives the scratch dir.

    Runs before verification so a replay whose source file was already pruned can
    still be checked against the durable copy. The target name is derived from the
    digest, so a retry lands on the same file and the stored payload stays byte
    identical, which is what the idempotency check compares.

    Never raises: a failure to copy leaves the receipt untouched, and the normal
    verification then reports the real problem.
    """
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        return
    path = verification.get("evidence_path")
    digest = verification.get("evidence_sha256")
    if not isinstance(path, str) or not isinstance(digest, str) or len(digest) < 16:
        return
    source = Path(path)
    target_dir = _evidence_root() / _safe_component(task_id)
    target = target_dir / f"{_safe_component(digest[:16])}{source.suffix or '.json'}"
    try:
        if not target.exists():
            if not source.exists():
                return
            target_dir.mkdir(parents=True, exist_ok=True)
            staging = target.with_name(target.name + ".tmp")
            staging.write_bytes(source.read_bytes())
            os.replace(staging, target)
        verification.setdefault("source_path", str(source))
        verification["evidence_path"] = str(target)
    except OSError:
        return


def _verify_evidence(receipt: dict) -> None:
    verification = receipt.get("verification")
    if not isinstance(verification, dict):
        raise OutcomeReceiptError("receipt verification must be an object")
    passed, failed, skipped, exit_code = (
        verification.get(k) for k in ("passed", "failed", "skipped", "exit_code")
    )
    values = (passed, failed, skipped, exit_code)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise OutcomeReceiptError("verification counts and exit_code must be integers")
    if passed < 1 or failed != 0 or skipped < 0 or exit_code != 0:
        raise OutcomeReceiptError("verification must include a passing result and zero failures")
    path = verification.get("evidence_path")
    digest = verification.get("evidence_sha256")
    if not isinstance(path, str) or not Path(path).is_absolute() or not isinstance(digest, str):
        raise OutcomeReceiptError("verification requires an absolute evidence path and SHA-256")
    evidence_path = Path(path)
    try:
        raw = evidence_path.read_bytes()
    except OSError as exc:
        raise OutcomeReceiptError(f"verification evidence is unreadable: {path}") from exc
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise OutcomeReceiptError("verification evidence digest mismatch")
    try:
        saved = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeReceiptError("verification evidence must be JSON") from exc
    if any(saved.get(key) != verification[key] for key in ("passed", "failed", "skipped", "exit_code")):
        raise OutcomeReceiptError("verification fields do not match the saved result artifact")
    refs = receipt.get("evidence")
    if not isinstance(refs, list) or not refs:
        raise OutcomeReceiptError("receipt must include at least one evidence reference")
    seen: set[str] = set()
    for item in refs:
        if not isinstance(item, dict):
            raise OutcomeReceiptError("evidence references must be objects")
        ref, ref_hash = item.get("path"), item.get("sha256")
        if not isinstance(ref, str) or not Path(ref).is_absolute() or not isinstance(ref_hash, str):
            raise OutcomeReceiptError("evidence reference requires an absolute path and SHA-256")
        if ref in seen:
            raise OutcomeReceiptError("duplicate evidence reference")
        seen.add(ref)
        try:
            actual_ref = hashlib.sha256(Path(ref).read_bytes()).hexdigest()
        except OSError as exc:
            raise OutcomeReceiptError(f"evidence reference is unreadable: {ref}") from exc
        if actual_ref != ref_hash:
            raise OutcomeReceiptError(f"evidence digest mismatch: {ref}")


def _checkpoint(conn: sqlite3.Connection, task_id: str, run_id: int, tenant: Any,
                token: str, payload: dict) -> int:
    _verify_owner(conn, task_id, run_id, tenant, token)
    raw, digest = _json_digest(payload)
    cur = conn.execute(
        "INSERT INTO task_checkpoints(task_id,run_id,tenant,owner_token,payload_json,digest,created_at) "
        "VALUES(?,?,?,?,?,?,?)", (task_id, run_id, tenant, token, raw, digest, int(time.time())),
    )
    kb._append_event(conn, task_id, "checkpoint_saved", {"checkpoint_id": cur.lastrowid,
        "digest": digest}, run_id=run_id)
    return int(cur.lastrowid)


def checkpoint_worker(conn: sqlite3.Connection, task_id: str, run_id: int, token: str,
                      checkpoint: dict | None, *, note: str | None = None) -> int:
    """Persist worker supplied progress at heartbeat or an automatic safe point."""
    ensure_schema(conn)
    task = kb.get_task(conn, task_id)
    payload = dict(checkpoint or {})
    payload.setdefault("version", 1)
    payload.update(task_id=task_id, run_id=int(run_id), tenant=task.tenant if task else None)
    payload.setdefault("completed_steps", [])
    payload.setdefault("evidence", [])
    payload.setdefault("next_action", note or "continue_current_task")
    payload.setdefault("pending_dependency", None)
    payload.setdefault("waiting_reason", None)
    payload.setdefault("recovery_owner", "kanban_dispatcher")
    payload.setdefault("recovery_trigger", "next_dispatch_tick")
    payload.setdefault("last_error", None)
    if not isinstance(payload["completed_steps"], list) or not isinstance(payload["evidence"], list):
        raise OutcomeReceiptError("checkpoint steps and evidence must be arrays")
    if not isinstance(payload["next_action"], str) or not payload["next_action"].strip():
        raise OutcomeReceiptError("checkpoint next_action is required")
    if len(json.dumps(payload, ensure_ascii=False)) > 64000:
        raise OutcomeReceiptError("checkpoint exceeds the 64 KB limit")
    with kb.write_txn(conn, allow_nested=True):
        return _checkpoint(conn, task_id, run_id, task.tenant if task else None, token, payload)


def checkpoint_recovery(conn: sqlite3.Connection, task_id: str, run_id: int, token: str,
                        reason: str, trigger: str) -> int:
    ensure_schema(conn)
    task = kb.get_task(conn, task_id)
    payload = latest_checkpoint(conn, task_id) or {}
    payload.update(version=1, task_id=task_id, run_id=int(run_id),
        tenant=task.tenant if task else None, next_action="resume_saved_progress",
        waiting_reason=None, recovery_owner="kanban_dispatcher", recovery_trigger=trigger,
        last_error=reason)
    payload.setdefault("completed_steps", [])
    payload.setdefault("evidence", [])
    with kb.write_txn(conn, allow_nested=True):
        return _checkpoint(conn, task_id, run_id, task.tenant if task else None, token, payload)


def accept_outcome(conn: sqlite3.Connection, task_id: str, run_id: int, token: str,
                   receipt: dict, handoff: dict) -> tuple[int, bool]:
    """Durably accept verified, task-bound proof before a canonical transition."""
    ensure_schema(conn)
    if not isinstance(receipt, dict) or receipt.get("version") != 1:
        raise OutcomeReceiptError("outcome_receipt version 1 is required")
    for name, expected in (("task_id", task_id), ("run_id", int(run_id))):
        if receipt.get(name) != expected:
            raise OutcomeReceiptError(f"outcome_receipt {name} does not match this worker")
    if receipt.get("disposition") not in {"complete", "review"}:
        raise OutcomeReceiptError("outcome_receipt disposition must be complete or review")
    if receipt.get("external_effect_state") != "none":
        raise OutcomeReceiptError("external outcome is ambiguous and cannot be auto-reconciled")
    idempotency_key = receipt.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
        raise OutcomeReceiptError("a bounded idempotency_key is required")
    # Persist before the payload digest so the stored row points at the durable
    # copy. Verification itself stays after the replay check: a retry that is
    # byte identical to the stored row must not fail because the source tree it
    # referenced has since been cleaned.
    _persist_evidence(receipt, task_id)
    stored = {"receipt": receipt, "handoff": handoff}
    raw, _ = _json_digest(stored)
    input_digest = task_input_digest(conn, task_id)
    if receipt.get("input_sha256") != input_digest:
        raise OutcomeReceiptError("receipt input digest does not match current task input")
    previous = conn.execute(
        "SELECT id,idempotency_key,payload_json FROM task_outcome_receipts "
        "WHERE task_id=? AND run_id=?", (task_id, int(run_id)),
    ).fetchone()
    if previous:
        if previous["idempotency_key"] == idempotency_key and previous["payload_json"] == raw:
            return int(previous["id"]), True
        raise OutcomeReceiptError("a conflicting outcome receipt already owns this task run")
    _verify_evidence(receipt)
    _verify_owner(conn, task_id, run_id, receipt.get("tenant"), token)
    with kb.write_txn(conn):
        previous = conn.execute(
            "SELECT id,idempotency_key,payload_json,state FROM task_outcome_receipts "
            "WHERE task_id=? AND run_id=?", (task_id, int(run_id)),
        ).fetchone()
        if previous:
            if previous["idempotency_key"] == idempotency_key and previous["payload_json"] == raw:
                return int(previous["id"]), True
            raise OutcomeReceiptError("a conflicting outcome receipt already owns this task run")
        _verify_owner(conn, task_id, run_id, receipt.get("tenant"), token)
        cur = conn.execute(
            "INSERT INTO task_outcome_receipts(task_id,run_id,tenant,owner_token,input_digest,"
            "idempotency_key,disposition,payload_json,state,accepted_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (task_id, int(run_id), receipt.get("tenant"), token, input_digest, idempotency_key,
             receipt["disposition"], raw, "accepted", int(time.time())),
        )
        checkpoint = {"version": 1, "task_id": task_id, "run_id": int(run_id),
            "tenant": receipt.get("tenant"), "completed_steps": receipt.get("completed_steps", []),
            "evidence": receipt["evidence"], "next_action": "reconcile_accepted_outcome",
            "pending_dependency": None, "waiting_reason": None, "recovery_owner": "kanban_dispatcher",
            "recovery_trigger": "next_dispatch_tick", "last_error": None}
        _checkpoint(conn, task_id, run_id, receipt.get("tenant"), token, checkpoint)
        kb._append_event(conn, task_id, "outcome_accepted", {"receipt_id": cur.lastrowid,
            "idempotency_key": idempotency_key, "disposition": receipt["disposition"]}, run_id=run_id)
        return int(cur.lastrowid), False


def reconcile_receipt(conn: sqlite3.Connection, receipt_id: int) -> bool:
    """Use canonical lifecycle APIs; exact terminal replays only read state."""
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM task_outcome_receipts WHERE id=?", (int(receipt_id),)).fetchone()
    if row is None:
        return False
    task = kb.get_task(conn, row["task_id"])
    payload = json.loads(row["payload_json"])
    disposition = row["disposition"]
    target = "done" if disposition == "complete" else "review"
    if task is None or task.tenant != row["tenant"] or task_input_digest(conn, task.id) != row["input_digest"]:
        return False
    if task.status == target:
        with kb.write_txn(conn):
            conn.execute("UPDATE task_outcome_receipts SET state='reconciled',reconciled_at=COALESCE(reconciled_at,?) "
                "WHERE id=?", (int(time.time()), int(receipt_id)))
        return True
    if task.status not in {"running", "ready"}:
        return False
    expected = task.current_run_id if task.status == "running" else None
    handoff = payload["handoff"]
    if disposition == "complete":
        ok = kb.complete_task(conn, task.id, result=handoff.get("result"), summary=handoff.get("summary"),
            metadata=handoff.get("metadata"), created_cards=handoff.get("created_cards"),
            expected_run_id=expected, force=expected is None)
    else:
        ok = kb.request_review(conn, task.id, summary=handoff.get("summary"), metadata=handoff.get("metadata"),
            reviewer=handoff.get("reviewer"), expected_run_id=expected, force=expected is None)
    if not ok:
        return False
    final = kb.get_task(conn, task.id)
    if final is None or final.status != target:
        return False
    run = kb.latest_run(conn, task.id)
    with kb.write_txn(conn):
        conn.execute("UPDATE task_outcome_receipts SET state='reconciled',reconciled_at=?,transition_run_id=? "
            "WHERE id=? AND state='accepted'", (int(time.time()), run.id if run else None, int(receipt_id)))
    return True


def reconcile_pending_outcomes(conn: sqlite3.Connection) -> list[int]:
    """Dispatcher hook: finish accepted work before it can be spawned again."""
    ensure_schema(conn)
    rows = conn.execute("SELECT id FROM task_outcome_receipts WHERE state='accepted' ORDER BY id").fetchall()
    done = []
    for row in rows:
        if reconcile_receipt(conn, int(row["id"])):
            done.append(int(row["id"]))
    return done


def accept_and_reconcile(conn: sqlite3.Connection, task_id: str, run_id: int, token: str,
                         receipt: dict, handoff: dict) -> int:
    receipt_id, _ = accept_outcome(conn, task_id, run_id, token, receipt, handoff)
    task = kb.get_task(conn, task_id)
    target = "done" if receipt.get("disposition") == "complete" else "review"
    if task and task.status == target:
        reconcile_receipt(conn, receipt_id)
        return receipt_id
    _verify_owner(conn, task_id, run_id, receipt.get("tenant"), token)
    if not reconcile_receipt(conn, receipt_id):
        raise OutcomeReceiptError("accepted receipt remains pending canonical reconciliation")
    return receipt_id


def latest_checkpoint(conn: sqlite3.Connection, task_id: str) -> dict | None:
    ensure_schema(conn)
    row = conn.execute("SELECT payload_json,digest FROM task_checkpoints WHERE task_id=? ORDER BY id DESC LIMIT 1",
        (task_id,)).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"])
        if _json_digest(payload)[1] != row["digest"]:
            return {"integrity_error": "checkpoint digest mismatch"}
        return payload
    except (TypeError, json.JSONDecodeError):
        return {"integrity_error": "checkpoint JSON malformed"}
