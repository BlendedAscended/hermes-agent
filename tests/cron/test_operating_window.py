"""Scheduled work waits for the next permitted local window without being lost."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from cron import jobs, operating_window


@pytest.fixture
def window_store(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")
    policy = tmp_path / "policy.json"
    monkeypatch.setattr(operating_window, "policy_path", lambda: policy)
    policy.write_text(json.dumps({
        "version": 1, "enabled": True, "timezone": "America/New_York",
        "activated_at": "2026-11-16T00:00:00+00:00",
        "windows": [{"start": "10:00", "end": "15:00"},
                    {"start": "16:00", "end": "19:30"}],
    }))
    return policy


@pytest.mark.parametrize("utc_time,expected", [
    ("2026-11-16T14:59:59+00:00", False),
    ("2026-11-16T15:00:00+00:00", True),
    ("2026-11-16T19:59:59+00:00", True),
    ("2026-11-16T20:00:00+00:00", False),
    ("2026-11-16T21:00:00+00:00", True),
    ("2026-11-17T00:30:00+00:00", False),
    ("2026-06-01T14:00:00+00:00", True),
])
def test_policy_boundaries_and_daylight_time(window_store, utc_time, expected):
    assert operating_window.scheduled_dispatch_allowed(datetime.fromisoformat(utc_time)) is expected


def _job(kind, scheduled, **extra):
    schedule = {"kind": "interval", "minutes": 60} if kind == "interval" else {"kind": "once"}
    return {"id": "windowjob", "name": "windowjob", "prompt": "x", "schedule": schedule,
            "next_run_at": scheduled.isoformat(), "last_run_at": None, "enabled": True,
            "state": "scheduled", "repeat": {"times": None, "completed": 0},
            "deliver": "local", **extra}


@pytest.mark.parametrize("kind", ["interval", "once"])
def test_deferred_due_job_survives_until_next_window(window_store, monkeypatch, kind):
    blocked = datetime.fromisoformat("2026-11-16T20:10:00+00:00")
    scheduled = blocked - timedelta(minutes=1)
    jobs.save_jobs([_job(kind, scheduled)])
    monkeypatch.setattr(jobs, "_hermes_now", lambda: blocked)
    assert jobs.get_due_jobs() == []
    stored = jobs.load_jobs()[0]
    assert stored["next_run_at"] == scheduled.isoformat()
    assert stored["window_deferred_slot"] == scheduled.isoformat()

    monkeypatch.setattr(jobs, "_hermes_now", lambda: datetime.fromisoformat("2026-11-16T21:00:00+00:00"))
    due = jobs.get_due_jobs()
    assert [job["id"] for job in due] == ["windowjob"]
    assert jobs.load_jobs()[0].get("window_deferred_slot") is None


def test_manual_trigger_remains_available(window_store, monkeypatch):
    blocked = datetime.fromisoformat("2026-11-16T20:10:00+00:00")
    monkeypatch.setattr(jobs, "_hermes_now", lambda: blocked)
    jobs.save_jobs([_job("interval", blocked, manual_run_at=blocked.isoformat())])
    assert [job["id"] for job in jobs.get_due_jobs()] == ["windowjob"]


def test_external_scheduler_cannot_claim_outside_window(window_store, monkeypatch):
    blocked = datetime.fromisoformat("2026-11-16T20:10:00+00:00")
    monkeypatch.setattr(jobs, "_hermes_now", lambda: blocked)
    jobs.save_jobs([_job("interval", blocked)])
    assert jobs.claim_job_for_fire("windowjob") is False
    stored = jobs.load_jobs()[0]
    assert stored["next_run_at"] == blocked.isoformat()
    assert stored["window_deferred_slot"] == blocked.isoformat()
    assert not stored.get("fire_claim")
    assert jobs.claim_job_for_fire("windowjob", manual=True) is True


def test_one_shot_recovers_if_host_slept_through_the_closed_window(window_store, monkeypatch):
    scheduled = datetime.fromisoformat("2026-11-16T20:10:00+00:00")
    monkeypatch.setattr(jobs, "_hermes_now", lambda: datetime.fromisoformat("2026-11-16T21:00:00+00:00"))
    jobs.save_jobs([_job("once", scheduled)])
    assert [job["id"] for job in jobs.get_due_jobs()] == ["windowjob"]


def test_policy_does_not_resurrect_pre_activation_occurrences(window_store, monkeypatch):
    scheduled = datetime.fromisoformat("2026-11-15T20:10:00+00:00")
    monkeypatch.setattr(jobs, "_hermes_now", lambda: datetime.fromisoformat("2026-11-16T21:00:00+00:00"))
    jobs.save_jobs([_job("once", scheduled)])
    assert jobs.get_due_jobs() == []


def test_deferred_recurring_work_is_recovered_even_if_general_catch_up_is_disabled(window_store, monkeypatch):
    scheduled = datetime.fromisoformat("2026-11-16T20:10:00+00:00")
    monkeypatch.setattr(jobs, "_hermes_now", lambda: datetime.fromisoformat("2026-11-16T21:00:00+00:00"))
    original = jobs._cron_config_number
    monkeypatch.setattr(jobs, "_cron_config_number", lambda key, *args:
                        False if key == "catch_up_missed" else original(key, *args))
    jobs.save_jobs([_job("interval", scheduled)])
    assert [job["id"] for job in jobs.get_due_jobs()] == ["windowjob"]


def test_missing_policy_preserves_existing_behavior_and_corrupt_policy_fails_closed(window_store):
    now = datetime.now(UTC)
    window_store.unlink()
    assert operating_window.scheduled_dispatch_allowed(now)
    window_store.write_text("broken")
    assert not operating_window.scheduled_dispatch_allowed(now)
