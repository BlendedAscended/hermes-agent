"""Automatic bounded re-runs for scheduled fires that never reached the model.

Inspired by Claude Cowork (desktop changelog v1.46388.1, 2026-09-04): "automatic re-runs
(after 5, 15, and 30 minutes) for a scheduled task that could not reach the model at all,
for example right after the computer wakes behind a VPN."

The class is deliberately narrow: the run must have FAILED with a transient network /
DNS error (``cron.scheduler_preflight._is_transient_provider_resolve_error``) AND the
agent must have completed zero API calls. Nothing was executed and nothing was spent, so
re-running cannot double a side effect — unlike a generic failure retry (see PR #16512),
which has to answer for one-shot dispatch accounting and mid-run side effects. Recurring
jobs only: finite one-shots are pre-claimed by ``claim_dispatch`` (at-most-times, #38758)
and must not regain a consumed dispatch here.

After the ladder, re-runs continue every ``STEADY_RETRY_SECONDS`` until the network returns or
the schedule's own next occurrence arrives. A laptop that wakes offline, or stays offline all
morning, then still does the day's work once it reconnects instead of skipping a full period.

While a retry is pending the failure notice is suppressed (Cowork re-runs silently); a
run that reaches the model — success or not — resets the ladder. Disable with
``cron.retry_unreachable: false`` in config.yaml.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from cron.constants import is_recurring
from hermes_time import now as _hermes_now

logger = logging.getLogger("cron.scheduler")

# Cowork's ladder: re-run after 5, 15, then 30 minutes.
RETRY_DELAYS_SECONDS: tuple[int, ...] = (300, 900, 1800)
# Past the ladder, keep re-running at this interval; the natural occurrence bounds the phase.
# Spend-neutral like the ladder: a re-run only follows a failure with zero model calls.
STEADY_RETRY_SECONDS = 1800

# Persisted on the job while a retry cycle is active: {"attempt": <1-based count of
# retries already scheduled>, "at": <ISO instant of the pending retry>, "expr": <the cron
# expression it was planned under>}. Cleared by any run
# that reached the model.
STATE_KEY = "unreachable_retry"


def retry_enabled(cfg: Optional[dict] = None) -> bool:
    """``cron.retry_unreachable`` — default ON (spend-neutral: only fires when zero
    model calls were made)."""
    if cfg is None:
        try:
            from hermes_cli.config import load_config

            cfg = load_config() or {}
        except Exception:  # config unreadable — keep the reliability default
            return True
    cron_cfg = (cfg or {}).get("cron")
    if not isinstance(cron_cfg, dict):
        return True
    return cron_cfg.get("retry_unreachable") is not False


def is_model_unreachable_failure(exc: BaseException, agent: Any = None) -> bool:
    """True when *exc* is a transient network/DNS failure and *agent* (may be ``None``)
    never completed a model call — the run consumed nothing and executed nothing."""
    if int(getattr(agent, "session_api_calls", 0) or 0) > 0:
        return False
    from cron.scheduler_preflight import _is_transient_provider_resolve_error

    if _is_transient_provider_resolve_error(exc):
        return True
    # A failed run_conversation returns only the offline summary text, which the cron runner
    # re-raises with no transport error in the chain to classify (missed on 2026-11-09).
    from agent.api_error_summary import OFFLINE_PROVIDER_SUMMARY

    return OFFLINE_PROVIDER_SUMMARY.lower() in str(exc).lower()


def _retry_delay(attempt: int) -> int:
    """Seconds until re-run number ``attempt + 1``: the ladder rung, then the steady interval."""
    if attempt < len(RETRY_DELAYS_SECONDS):
        return RETRY_DELAYS_SECONDS[attempt]
    return STEADY_RETRY_SECONDS


def _ladder_applies(job: dict[str, Any]) -> bool:
    """The ladder's applicability gate: recurring, not paused, and enabled in config."""
    return is_recurring(job) and job.get("state") != "paused" and retry_enabled()


def _ladder_instant(job: dict[str, Any], natural_next: Optional[datetime],
                    now: datetime) -> Optional[datetime]:
    """The ladder instant ``plan_retry`` parks for this flagged failure, or None when it
    parks nothing: not recurring, paused, disabled, or the schedule's own *natural_next*
    fires at or before the rung. The single source of that decision for ``plan_retry``
    and ``will_retry``."""
    attempt = int((job.get(STATE_KEY) or {}).get("attempt") or 0)
    if not _ladder_applies(job):
        return None
    # late: jobs imports this module's helpers
    from cron.jobs import _instant_at_or_before, _seconds_after

    retry_dt = _seconds_after(now, _retry_delay(attempt))
    if natural_next is not None and _instant_at_or_before(natural_next, retry_dt):
        return None  # the natural occurrence IS the retry
    return retry_dt


def will_retry(job: dict[str, Any]) -> bool:
    """True iff ``plan_retry`` will park a re-run for this flagged failure, so the scheduler
    may hold the interim notice. Called before ``mark_job_run``."""
    repeat = job.get("repeat") or {}
    times = repeat.get("times")
    counted = 0 if is_retry_run(job) else 1  # a ladder re-run does not count (``ladder_rung``)
    if times is not None and times > 0 and int(repeat.get("completed") or 0) + counted >= times:
        return False  # _advance_after_run completes the job; plan_retry never runs
    from cron.jobs import _parse_aware, compute_next_run

    now = _hermes_now()
    natural_next = _parse_aware(compute_next_run(job.get("schedule") or {}, now.isoformat()))
    if natural_next is None:
        # The natural occurrence is uncomputable (e.g. croniter missing): _advance_after_run
        # leaves the record state=error — terminal — so plan_retry never runs either.
        return False
    return _ladder_instant(job, natural_next, now) is not None


def clear_state(job: dict[str, Any]) -> None:
    """A run reached the model (any outcome): the ladder resets."""
    job.pop(STATE_KEY, None)


def is_retry_fire(job: dict[str, Any], next_run: str) -> bool:
    """True for the exact ladder instant parked by ``plan_retry`` (off the cron lattice).

    The expression fingerprint keeps a direct ``jobs.json`` schedule edit from inheriting the
    exception, as in ``cron.quota_hold.is_recovery_fire``.
    """
    state = job.get(STATE_KEY) or {}
    return state.get("at") == next_run and state.get("expr") == (job.get("schedule") or {}).get("expr")


def is_retry_run(job: dict[str, Any]) -> bool:
    """True when this dispatch snapshot fired the ladder instant parked by ``plan_retry``: a
    re-run of an occurrence that already counted toward ``repeat``, so it must not count again.
    Manual runs carry no ``_scheduled_instant`` and always count."""
    from cron.occurrences import scheduled_instant

    instant = job.get("_scheduled_instant")
    return instant is not None and scheduled_instant((job.get(STATE_KEY) or {}).get("at")) == instant


def plan_retry(job: dict[str, Any]) -> bool:
    """Called under the jobs lock AFTER ``_advance_after_run`` computed the schedule's
    natural ``next_run_at`` for a failed, flagged run. Pulls ``next_run_at`` earlier to
    the ladder instant when that is sooner than the natural occurrence; exhausted or
    inapplicable cycles clear state and leave the schedule untouched. Returns True when
    a retry was scheduled."""
    from cron.jobs import _parse_aware  # late: jobs imports this module's helpers

    retry_dt = _ladder_instant(job, _parse_aware(job.get("next_run_at")), _hermes_now())
    attempt = int((job.get(STATE_KEY) or {}).get("attempt") or 0)
    if retry_dt is None:
        if attempt > 0 and _ladder_applies(job):
            # The scheduled occurrence comes before the next re-run: it takes over, and the
            # reset gives it a fresh ladder if the network is still down.
            logger.warning(
                "Job '%s': model unreachable after %d automatic re-runs — the scheduled "
                "occurrence at %s takes over",
                job.get("name", job.get("id", "?")), attempt, job.get("next_run_at"))
        clear_state(job)
        return False
    delay = _retry_delay(attempt)
    retry_at = retry_dt.isoformat()
    job[STATE_KEY] = {"attempt": attempt + 1, "at": retry_at, "expr": job["schedule"].get("expr")}
    job["next_run_at"] = retry_at
    if job.get("state") != "paused":
        job["state"] = "scheduled"
    logger.info(
        "Job '%s': model unreachable with zero API calls — automatic re-run %d in %ds "
        "(at %s, until the network returns or the next scheduled occurrence)",
        job.get("name", job.get("id", "?")), attempt + 1, delay, retry_at)
    return True
