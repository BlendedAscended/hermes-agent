## 2026-10-30 01:34 (UTC) — Require tested Hermes main integration in Luna receipt

**Changes:**
- Bind task source paths to the goal baseline and require a verified local main integration record in the deterministic receipt.
- Updated `scripts/ci/finalize_luna_continuity_goal.py`; no runtime API or production configuration changed.
- The receipt remains incomplete until the narrow main port is tested and recorded.

## 2026-10-30 01:25 (UTC) — Add bounded runtime probes and source-bound continuity gate

**Changes:**
- Add bounded subprocess execution, source-hash and log-hash verification, and an empty changed-code health gate to the deterministic finalizer.
- Record read only launchd, process, cron, Hermes job, workflow, and gateway log evidence without reading process environments or persisting unrelated cron lines.
- Refactor receipt validation below the repository complexity threshold and replace the sweep regression test mutation ladder with data driven edits.
- Key files: scripts/ci/finalize_luna_continuity_goal.py, tests/skills/test_state_anchor_discriminator_sweep.py, tests/hermes_cli/test_kanban_runtime_policy.py.
- No schema migration, provider call, production mutation, gateway restart, deployment, or push.

## 2026-10-30 01:11 (UTC) — Add deterministic Luna continuity goal gate

**Changes:**
- Add a source and log hash validating gate for Hermes, Jack Exec, VFlow Ops Studio and AICT acceptance.
- Add a narrow runner allowlist entry for the disposable dispatcher proof artifact path.
- Preserve `auto_decompose: false`; no live provider call, production mutation, gateway restart, push or deployment occurred.

## 2026-10-29 23:20 (UTC) — Bind worker stop decisions to successful handoffs and exact run ownership

**Changes:**
- Extended existing guard edits with paired call/result acknowledgments, exact task and run matching, and read only checks against the dispatcher pinned board.
- Completed, handed off, expired and superseded workers stop without retained transcript acknowledgment; rejected or mismatched calls do not suppress recovery.
- Added real temporary board timeout, durable checkpoint, successor fencing, verified completion and review child promotion acceptance test. Existing timeout source edits preserved.
- Focused lifecycle verification: 83 passed, zero failed or skipped. Separate cap/protocol subset: 4 passed, 1 Linux-only test skipped on macOS.
- Broader recovery suite hits unchanged CLI bootstrap real-home manifest I/O; full checks retain timeout patch health and desktop archive cache failures. No live board or config mutation.

