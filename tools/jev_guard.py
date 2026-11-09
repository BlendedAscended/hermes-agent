"""Pre-execution Jev automode risk gate for Hermes terminal calls.

Wraps the synced ``~/.hermes/scripts/jev_automode_guard.py`` classifier inside
the terminal tool's unconditional ``_pre_exec_block`` so autonomous workers
(and sessions with ``HERMES_JEV_GUARD=1``) classify commands before running
them, regardless of ``force=True`` or approval prompts.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from agent.delegation_context import owned_kanban_task

logger = logging.getLogger("tools.jev_guard")

_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "disabled"})

_FALLBACK_CRITICAL_PATTERNS = (
    re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f[a-zA-Z]*\s+(?:/(?:\s|$|\*)|~(?:\s|$|/)|\$HOME(?:\s|$|/))"),
    re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*[rR][a-zA-Z]*\s+(?:/(?:\s|$|\*)|~(?:\s|$|/)|\$HOME(?:\s|$|/))"),
    re.compile(r"\bdrop\s+(?:database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
    re.compile(r"\bmkfs(?:\.\w+)?\b"),
    re.compile(r"\bdd\s+if="),
    re.compile(r"\bchmod\s+-[rR]\s+777\s+/"),
)


def _in_isolated_test() -> bool:
    return bool(os.environ.get("HERMES_TEST_ISOLATION") or os.environ.get("PYTEST_CURRENT_TEST"))


def jev_guard_enabled() -> bool:
    """Return True when the pre-execution Jev command guard should enforce.

    Enabled automatically for dispatcher-owned Kanban workers unless opted out
    via ``HERMES_JEV_GUARD=0``, and opt-in for any session with
    ``HERMES_JEV_GUARD=1``.
    """
    raw = (os.environ.get("HERMES_JEV_GUARD") or "").strip().lower()
    if raw in _FALSE_WORDS:
        return False
    if raw in _TRUE_WORDS:
        return True
    return bool(owned_kanban_task())


def _network_allowed() -> bool:
    raw = (os.environ.get("HERMES_JEV_GUARD_NETWORK") or "").strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def _resolve_guard_script_path() -> Optional[Path]:
    override = (os.environ.get("HERMES_JEV_AUTOMODE_GUARD_PATH") or "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    try:
        from hermes_constants import get_hermes_home

        candidates.append(Path(get_hermes_home()) / "scripts" / "jev_automode_guard.py")
    except Exception:
        pass
    if not _in_isolated_test():
        candidates.append(Path.home() / ".hermes" / "scripts" / "jev_automode_guard.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_automode_guard() -> Any:
    script_path = _resolve_guard_script_path()
    if script_path is None:
        return None
    spec = importlib.util.spec_from_file_location("hermes_jev_automode_guard", script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fallback_check_command_safety(command: str) -> dict[str, Any]:
    cmd = (command or "").strip()
    for pat in _FALLBACK_CRITICAL_PATTERNS:
        if pat.search(cmd):
            return {
                "allowed": False,
                "risk_level": "critical_destruction",
                "is_destructive_confidence": 1.0,
                "source": "deterministic_rule",
                "reason": "Command matches critical destruction rule",
                "requires_human": True,
            }
    return {
        "allowed": True,
        "risk_level": "project_mutation",
        "is_destructive_confidence": 0.0,
        "source": "fallback_safeguard",
        "reason": "External jev_automode_guard script unavailable; deterministic rule passed",
        "requires_human": False,
    }


def jev_automode_block(
    *,
    command: str,
    cwd: str,
    workdir: Optional[str],
    session_key: str,
) -> Optional[str]:
    """Return a JSON error envelope when ``jev_automode_guard`` blocks *command*."""
    if not jev_guard_enabled():
        return None
    try:
        guard_mod = _load_automode_guard()
    except Exception as exc:
        logger.warning("Failed to load jev_automode_guard: %s", exc)
        guard_mod = None

    if guard_mod is not None and hasattr(guard_mod, "check_command_safety"):
        try:
            verdict = guard_mod.check_command_safety(
                command,
                context={
                    "cwd": workdir or cwd,
                    "session_key": session_key,
                    "agent": "hermes-kanban" if owned_kanban_task() else "hermes",
                },
                allow_network=_network_allowed(),
            )
        except Exception as exc:
            logger.warning("jev_automode_guard evaluation failed: %s", exc)
            verdict = _fallback_check_command_safety(command)
    else:
        verdict = _fallback_check_command_safety(command)

    if verdict.get("allowed", True):
        return None

    risk_level = verdict.get("risk_level", "critical_destruction")
    reason = verdict.get("reason", "destructive command flagged for human approval")
    logger.warning(
        "Blocked terminal command via Jev automode guard (risk=%s, source=%s): %s",
        risk_level,
        verdict.get("source", "unknown"),
        reason,
    )
    return json.dumps(
        {
            "output": "",
            "exit_code": 1,
            "error": f"Blocked by Jev automode safety guard ({risk_level}): {reason}. Route destructive actions to human approval.",
            "status": "blocked",
            "requires_human": bool(verdict.get("requires_human", True)),
            "risk_level": risk_level,
            "source": verdict.get("source", "jev_automode_guard"),
        },
        ensure_ascii=False,
    )


__all__ = ["jev_automode_block", "jev_guard_enabled"]
