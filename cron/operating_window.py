"""Optional scheduled dispatch windows shared by all Hermes profiles on a host."""

from __future__ import annotations

import json
import logging
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from hermes_constants import get_default_hermes_root

logger = logging.getLogger(__name__)


def policy_path() -> Path:
    return get_default_hermes_root() / "state" / "automation-window.json"


def scheduled_dispatch_allowed(now: datetime) -> bool:
    """Missing policy preserves existing behavior; invalid policy fails closed."""
    path = policy_path()
    try:
        policy = json.loads(path.read_text())
    except FileNotFoundError:
        return True
    except (OSError, ValueError) as exc:
        logger.error("Scheduled dispatch window policy unreadable: %s", exc)
        return False
    try:
        if policy.get("enabled") is False:
            return True
        if policy.get("enabled") is not True or policy.get("version") != 1:
            raise ValueError("invalid policy version or enabled flag")
        local = now.astimezone(ZoneInfo(policy["timezone"]))
        windows = policy["windows"]
        if not isinstance(windows, list) or not windows:
            raise ValueError("windows must be a nonempty list")
        valid = []
        for item in windows:
            start = time.fromisoformat(item["start"])
            end = time.fromisoformat(item["end"])
            if start.tzinfo or end.tzinfo or start >= end:
                raise ValueError("invalid window")
            valid.append((start, end))
        local_time = local.timetz().replace(tzinfo=None)
        return any(start <= local_time < end for start, end in valid)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        logger.error("Invalid scheduled dispatch window policy: %s", exc)
        return False


def deferred_across_closed_window(scheduled: datetime, now: datetime) -> bool:
    """Recover a missed occurrence even if the host slept through the closed window.

    Only occurrences scheduled after policy activation are eligible, so installing
    the policy cannot resurrect arbitrary historical one-shot jobs.
    """
    try:
        policy = json.loads(policy_path().read_text())
        if policy.get("enabled") is not True or policy.get("version") != 1:
            return False
        activated = datetime.fromisoformat(policy["activated_at"])
        if activated.tzinfo is None or scheduled.tzinfo is None or now.tzinfo is None:
            return False
        if scheduled < activated or scheduled > now:
            return False
        tz = ZoneInfo(policy["timezone"])
        current = now.astimezone(tz)
        local_time = current.timetz().replace(tzinfo=None)
        for window in policy["windows"]:
            start = time.fromisoformat(window["start"])
            end = time.fromisoformat(window["end"])
            if start <= local_time < end:
                beginning = current.replace(hour=start.hour, minute=start.minute,
                                            second=start.second, microsecond=start.microsecond)
                return scheduled < beginning
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return False
