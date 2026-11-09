"""Cowork-inspired bounded automatic re-runs for cron fires that never reached the model.

Contract (cron/unreachable_retry.py): a recurring job whose run fails with a transient
network/DNS error before ANY model call gets its ``next_run_at`` pulled earlier along a
ladder (5/15/30 min), then every 30 minutes until the network returns or the schedule's
own next occurrence arrives; a run that reaches the model resets the ladder.
"""

from datetime import datetime, timedelta, timezone, UTC

import pytest

import cron.scheduler as sched
from cron import unreachable_retry as ur
from cron.jobs import create_job, get_due_jobs, get_job, load_jobs, mark_job_run, save_jobs


@pytest.fixture
def tmp_cron_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_unreachable_failure_pulls_next_run_earlier_then_retries_steadily(
    tmp_cron_home, monkeypatch,
):
    """Failed-unreachable runs re-fire on the 5/15/30-minute ladder instead of waiting a
    full period, then keep re-firing every 30 minutes rather than skipping to the schedule:
    an outage longer than the ladder must not cost the whole period. A cron job's ladder
    instant is off its lattice yet must be due, not re-anchored as a stale expression edit."""
    # Interval, not a cron expression: the natural next fire is always a full day out. A
    # fixed clock time ("0 3 * * *") makes the 30-minute rung land past the natural fire
    # in the half hour before it, and plan_retry rightly yields to the schedule (CI red).
    job = create_job("nightly report", "every 24h")
    job_id = job["id"]

    now = datetime.now(UTC)
    for i, delay in enumerate(ur.RETRY_DELAYS_SECONDS):
        assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
        j = get_job(job_id)
        nxt = datetime.fromisoformat(j["next_run_at"])
        # Pulled to roughly now + ladder delay, far before the daily occurrence.
        assert timedelta(0) < nxt - now <= timedelta(seconds=delay + 120), (
            f"attempt {i}: expected retry ~{delay}s out, got {nxt - now}")
        assert j[ur.STATE_KEY]["attempt"] == i + 1

    # Past the ladder: still offline, so re-run again in 30 minutes, not tomorrow.
    for extra in range(2):
        assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
        j = get_job(job_id)
        nxt = datetime.fromisoformat(j["next_run_at"])
        assert timedelta(0) < nxt - now <= timedelta(seconds=ur.STEADY_RETRY_SECONDS + 120)
        assert j[ur.STATE_KEY]["attempt"] == len(ur.RETRY_DELAYS_SECONDS) + extra + 1

    pinned = datetime(2026, 9, 18, 12, 1, tzinfo=UTC)
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: pinned)
    monkeypatch.setattr(ur, "_hermes_now", lambda: pinned)
    weekly = create_job("weekly digest", "0 12 * * 5")
    assert mark_job_run(weekly["id"], False, "ConnectError: dns", model_unreachable=True)
    retry_at = datetime.fromisoformat(get_job(weekly["id"])["next_run_at"])
    assert retry_at == pinned + timedelta(seconds=ur.RETRY_DELAYS_SECONDS[0])
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: retry_at + timedelta(seconds=1))
    assert weekly["id"] in {due["id"] for due in get_due_jobs()}

    # A direct jobs.json expression edit while a retry is parked re-anchors without firing.
    assert mark_job_run(weekly["id"], False, "ConnectError: dns", model_unreachable=True)
    retry_at = datetime.fromisoformat(get_job(weekly["id"])["next_run_at"])
    jobs = load_jobs()
    next(j for j in jobs if j["id"] == weekly["id"])["schedule"]["expr"] = "0 9 * * 1"
    save_jobs(jobs)
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: retry_at + timedelta(seconds=1))
    assert weekly["id"] not in {due["id"] for due in get_due_jobs()}


def test_will_retry_mirrors_plan_retry_yield(tmp_cron_home):
    """``will_retry`` answers True only when ``plan_retry`` would park a re-run. Called after
    ``mark_job_run`` — valid, the predictor reads only persisted job state."""
    fast = create_job("fast poll", "every 2m")
    assert mark_job_run(fast["id"], False, "ConnectError: dns", model_unreachable=True)
    j = get_job(fast["id"])
    assert j is not None
    assert j.get(ur.STATE_KEY) is None, "2m cadence beats the 5m rung: plan_retry yields"
    assert ur.will_retry(j) is False, "yielded: no re-run is scheduled, notice must go out"

    slow = create_job("nightly report", "every 24h")
    assert mark_job_run(slow["id"], False, "ConnectError: dns", model_unreachable=True)
    js = get_job(slow["id"])
    assert js is not None
    assert js[ur.STATE_KEY]["attempt"] == 1
    assert ur.will_retry(js) is True, "5m rung beats the 24h cadence: re-run is scheduled"

    mid = create_job("ten minute sync", "every 10m")
    assert mark_job_run(mid["id"], False, "ConnectError: dns", model_unreachable=True)
    jm = get_job(mid["id"])
    assert jm is not None
    # 10m cadence beats the 15m and 30m rungs: the ladder can never climb past attempt 1,
    # so the exhaustion escape is unreachable. At attempt 1 the next (15m) rung loses to the
    # 10m run, so this failure's notice goes out rather than being held for a retry.
    assert jm[ur.STATE_KEY]["attempt"] == 1
    assert ur.will_retry(jm) is False, "10m cadence beats the 15m rung: yielded, notice goes out"

    last = create_job("final run", "every 24h", repeat=1)
    assert ur.will_retry(get_job(last["id"])) is False, "final finite repeat completes the job"


def test_reaching_the_model_resets_ladder_and_oneshots_never_retry(tmp_cron_home):
    """Any run that reached the model clears retry state; one-shots (pre-claimed
    dispatch, at-most-times #38758) never enter the ladder."""
    job = create_job("hourly sync", "every 12h")
    job_id = job["id"]
    assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
    assert get_job(job_id)[ur.STATE_KEY]["attempt"] == 1

    # A normal failed run (model reached) resets the ladder and stays on schedule.
    assert mark_job_run(job_id, False, "agent error")
    j = get_job(job_id)
    assert j.get(ur.STATE_KEY) is None
    now = datetime.now(UTC)
    assert datetime.fromisoformat(j["next_run_at"]) - now > timedelta(hours=11)

    # One-shot: flag is ignored, no retry state, no resurrection.
    once = create_job("one shot", _iso(datetime.now(UTC) + timedelta(minutes=1)))
    assert mark_job_run(once["id"], False, "ConnectError: dns", model_unreachable=True)
    remaining = get_job(once["id"])
    assert remaining is None or remaining.get(ur.STATE_KEY) is None


@pytest.mark.parametrize("rung_tail", ["finish", "crash", "worker", "ownership_lost"])
def test_ladder_reruns_do_not_spend_extra_repeat_budget(tmp_cron_home, monkeypatch, rung_tail):
    """A ladder re-run repeats an occurrence that already counted toward ``repeat``, so it must
    not count again. Otherwise an outage that outlasts the ladder retires a finite job with zero
    model calls, where the same outage with the ladder off costs it one run (#109990 fixed only
    the final-run notice). Drives the tick's claim hand-off and bookkeeping tail: the claimed
    snapshot's ``next_run_at`` has already moved to the natural slot when the run is recorded."""
    clock = [datetime.now(UTC)]
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: clock[0])
    monkeypatch.setattr(ur, "_hermes_now", lambda: clock[0])
    monkeypatch.setattr(sched, "finish_execution", lambda *_a, **_kw: None)
    runs = []
    monkeypatch.setattr(sched, "run_one_job", lambda job, **_kw: runs.append(job) or True)
    # "crash": a rung whose run raises leaves through the crash tail, which must not count it either.
    monkeypatch.setattr(sched, "run_job", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(sched, "mark_execution_running", lambda *_a, **_kw: {})
    monkeypatch.setattr(sched, "_deliver_crash_failure", lambda *_a, **_kw: (None, "suppressed"))
    job_id = create_job("digest", "every 24h", repeat=2)["id"]

    held = []
    for _ in range(1 + len(ur.RETRY_DELAYS_SECONDS)):  # the occurrence, then every rung
        clock[0] = datetime.fromisoformat(get_job(job_id)["next_run_at"]) + timedelta(seconds=1)
        due = next(d for d in get_due_jobs() if d["id"] == job_id)
        assert sched._process_due_job(dict(due, execution_id="exec"), None, None, False)
        run = runs[-1]
        if rung_tail == "crash" and held:
            assert sched._run_one_job_body(run) is False
            assert get_job(job_id)["repeat"]["completed"] == 1, "a crashed rung must not count"
            return
        if rung_tail == "worker" and held:
            # A rung whose restart-safe worker died is recovered as an unknown outcome.
            monkeypatch.setattr(
                sched, "get_execution", lambda *_a, **_kw: {"status": "unknown", "error": "worker died"})
            from cron.scheduler_worker_failure import record_unknown_worker_outcome
            assert record_unknown_worker_outcome(run)
            assert get_job(job_id)["repeat"]["completed"] == 1, "a dead-worker rung must not count"
            return
        if rung_tail == "ownership_lost" and held:
            # A rung cancelled at the transport (dashboard drain) is recorded as interrupted.
            import threading
            cancelled = threading.Event()
            cancelled.set()
            monkeypatch.setattr(sched, "run_job", lambda *_a, **_kw: (True, "out", "done", None))
            assert sched._run_one_job_body(run, transport_cancel=cancelled) is True
            j = get_job(job_id)
            assert j["last_error"] == sched._OWNERSHIP_LOST_INTERRUPTED
            assert j["repeat"]["completed"] == 1, "an interrupted rung must not count"
            return
        run["_model_unreachable"] = True
        held.append(ur.will_retry(run))
        assert sched._finish_completed_run(
            sched._RunDelivery(job=run, success=False, error="ConnectError: dns"),
            run["fire_claim"]["by"], "exec")
        assert get_job(job_id)["repeat"]["completed"] == 1, "only the occurrence itself counts"

    j = get_job(job_id)
    # Still offline after the ladder: a steady re-run is parked 30 minutes out, not the next day.
    assert j["state"] == "scheduled"
    assert j[ur.STATE_KEY]["attempt"] == len(ur.RETRY_DELAYS_SECONDS) + 1
    assert datetime.fromisoformat(j["next_run_at"]) - clock[0] <= timedelta(
        seconds=ur.STEADY_RETRY_SECONDS + 120)
    # A rung never completes the job, so every notice is held while a re-run is pending.
    assert held == [True, True, True, True]
    # But a rung whose limit was edited down to the count does retire the job: send its notice.
    assert not ur.will_retry(dict(runs[1], repeat={"times": 1, "completed": 1}))


def test_offline_summary_counts_as_model_unreachable():
    """A failed run_conversation hands the cron runner only the agent's offline summary, with no
    transport error in the chain. It must still enter the ladder (missed on 2026-11-09, when a
    morning offline cost every daily content job its run)."""
    from types import SimpleNamespace

    from agent.api_error_summary import OFFLINE_PROVIDER_SUMMARY

    offline = RuntimeError(OFFLINE_PROVIDER_SUMMARY)
    assert ur.is_model_unreachable_failure(offline, None)
    assert ur.is_model_unreachable_failure(offline, SimpleNamespace(session_api_calls=0))
    # A model call already happened: re-running could repeat its side effects.
    assert not ur.is_model_unreachable_failure(offline, SimpleNamespace(session_api_calls=1))
    assert not ur.is_model_unreachable_failure(RuntimeError("agent reported failure"), None)


def test_late_open_offline_catch_up_retries_until_network_returns(tmp_cron_home, monkeypatch):
    """The laptop sleeps through a daily 06:00 job and wakes the next afternoon before Wi-Fi is
    back. The catch-up fires once, fails offline, and re-runs (5m, 15m, 30m, then every 30m)
    instead of waiting for the next 06:00. The first run that reaches the model ends the cycle
    and the job returns to its schedule."""
    clock = [datetime(2026, 11, 9, 5, 0, tzinfo=UTC)]
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: clock[0])
    monkeypatch.setattr(ur, "_hermes_now", lambda: clock[0])
    job_id = create_job("signal scan", "0 6 * * *")["id"]

    clock[0] = datetime(2026, 11, 10, 14, 0, tzinfo=UTC)  # opened a day late
    assert job_id in {d["id"] for d in get_due_jobs()}, "a missed daily run catches up once"

    for delay in (*ur.RETRY_DELAYS_SECONDS, ur.STEADY_RETRY_SECONDS):
        assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
        retry_at = datetime.fromisoformat(get_job(job_id)["next_run_at"])
        assert retry_at == clock[0] + timedelta(seconds=delay)
        clock[0] = retry_at + timedelta(seconds=1)
        assert job_id in {d["id"] for d in get_due_jobs()}, "the parked re-run is due"

    # The network is back: the run succeeds, the ladder clears, the schedule resumes.
    assert mark_job_run(job_id, True)
    j = get_job(job_id)
    assert j.get(ur.STATE_KEY) is None
    assert datetime.fromisoformat(j["next_run_at"]) == datetime(2026, 11, 11, 6, 0, tzinfo=UTC)


def test_scheduled_occurrence_takes_over_from_steady_retries(tmp_cron_home, monkeypatch):
    """Steady re-runs never fire past the schedule's own next occurrence: when that comes
    first, it runs instead and the retry state resets for a fresh ladder."""
    clock = [datetime(2026, 11, 9, 5, 45, tzinfo=UTC)]
    monkeypatch.setattr("cron.jobs._hermes_now", lambda: clock[0])
    monkeypatch.setattr(ur, "_hermes_now", lambda: clock[0])
    job_id = create_job("hourly digest", "0 * * * *")["id"]
    jobs = load_jobs()
    rec = next(j for j in jobs if j["id"] == job_id)
    rec[ur.STATE_KEY] = {"attempt": len(ur.RETRY_DELAYS_SECONDS), "at": None,
                         "expr": rec["schedule"]["expr"]}
    save_jobs(jobs)

    assert mark_job_run(job_id, False, "ConnectError: dns", model_unreachable=True)
    j = get_job(job_id)
    assert j.get(ur.STATE_KEY) is None
    assert datetime.fromisoformat(j["next_run_at"]) == datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
