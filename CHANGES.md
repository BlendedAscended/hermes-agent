## 2026-11-09 20:06 (UTC) — cron: offline runs now retry until the network returns instead of skipping the period

**Changes:**
- Root cause (2026-11-09): a failed run_conversation hands the cron runner only the agent's offline summary ("Hermes can't reach the model provider..."), with no transport error in the chain. cron.unreachable_retry never classified it, so the 5/15/30 minute ladder never fired and every daily content job lost its run for the day.
- is_model_unreachable_failure now also matches agent.api_error_summary.OFFLINE_PROVIDER_SUMMARY (new shared constant; the summary text itself is unchanged). Still requires zero model calls, so re-runs stay spend neutral.
- After the 5/15/30 minute ladder, re-runs continue every 30 minutes (STEADY_RETRY_SECONDS) until the network returns or the schedule's next occurrence, which then takes over with a fresh ladder. Missed runs while the laptop sleeps already catch up once on wake (cron.catch_up_missed); that catch up now survives waking before Wi-Fi reconnects.
- Files: cron/unreachable_retry.py, agent/api_error_summary.py, tests/cron/test_unreachable_retry.py (ladder exhaustion tests rewritten to the new intent; added offline summary, late open catch up and occurrence takeover tests).
- Disable with cron.retry_unreachable: false. No config or schema migration.

## 2026-11-09 18:04 (UTC) — Integrate Jev pre-execution terminal guard and agent loop step evaluator

**Changes:**
- Added `tools/jev_guard.py` and wired `jev_automode_block` into `tools/terminal_tool_guards.py` and `tools/terminal_tool.py::_pre_exec_block` so opt-in `HERMES_JEV_GUARD=1` checks block critical destruction commands using Layer 1 deterministic rules and `jev_automode_guard.py`.
- Added `agent/jev_step_evaluator.py` and wired `assess_tool_round` into `agent/turn_tool_round.py`, `agent/conversation_loop.py`, `agent/kanban_stop.py`, and `agent/turn_stop_gates.py` for opt-in `HERMES_JEV_STEP_EVAL=1` completion/fatal-error checks with prompt-cache preservation and Kanban handoff safety.
- Added unit tests in `tests/tools/test_terminal_jev_guard.py` and `tests/agent/test_jev_step_evaluator.py`; verified 75 passed across 6 test suites and clean ruff pre-commit lint.

## 2026-10-30 06:45 (UTC) — Fix Luna deterministic gate path and baseline exit checks

**Changes:**
- Resolve the VFlow task tsconfig to an absolute path and expect the observed unchanged full typecheck diagnostic to exit 1.
- Updated scripts/ci/finalize_luna_continuity_goal.py; no runtime behavior or production configuration changed.

## 2026-10-30 05:58 (UTC) — Bind Luna gate to focused source and acceptance proof

**Changes:**
- Corrected the Jack Exec source pin and narrowed its runner to the documented lease and outbox suites; parse Node TAP counts with intervening suite metadata.
- Self test valid and invalid count evidence and local main integration status, record the full task scoped VFlow typecheck, and qualify its unchanged project typecheck diagnostic by source hash.
- Compare installed worker skills to their recorded profile hashes and allow live truth probes to finish without an arbitrary 90 second timeout.
- Updated `scripts/ci/finalize_luna_continuity_goal.py`; no runtime, provider, or production configuration changed.

## 2026-10-30 01:34 (UTC) — Check Hermes changed-code health across the full goal range

**Changes:**
- Run the changed-code health gate from the Hermes goal baseline through the current source commit so verifier-only commits cannot hide findings in earlier task packages.
- Updated `scripts/ci/finalize_luna_continuity_goal.py`; no runtime or production behavior changed.
- Verified the full goal range returns an empty findings list.

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

