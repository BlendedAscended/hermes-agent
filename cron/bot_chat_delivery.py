"""Live-process bridge for gateways booted on 2397a2e49d..(fix for t_dd5ef97f).

The queued Bot Chat delivery lane was retired in 1cc9cb9199; leftover records are drained by
``cron.bot_chat_legacy.drain_legacy_pending`` during tick housekeeping. Commit 2397a2e49d replayed
a patch from an older base that re-imported this module inside ``scheduler_tick._tick_admitted``,
so every in-window tick of an already-running gateway raises ModuleNotFoundError and no job is
dispatched. The fixed ``scheduler_tick`` no longer imports this module. This file exists only so a
gateway process that loaded the broken ``scheduler_tick`` recovers without a fleet-wide restart.

Delete this file once no gateway booted before the fix is running (any gateway restart clears it).
Both functions are deliberate no-ops: there is no queued lane left to drain.
"""

from __future__ import annotations


def drain() -> None:
    """No-op: the queued Bot Chat delivery lane is retired (see module docstring)."""
    return


def drain_in_background() -> None:
    """No-op: the queued Bot Chat delivery lane is retired (see module docstring)."""
    return
