"""Pre-execution Jev automode risk gate for Hermes terminal calls.

Wraps the synced ``~/.hermes/scripts/jev_automode_guard.py`` classifier inside
the terminal tool's unconditional ``_pre_exec_block`` so autonomous workers
(and sessions with ``HERMES_JEV_GUARD=1``) classify commands before running
them, regardless of ``force=True`` or approval prompts.
"""

from __future__ import annotations

from datetime import datetime, timezone, UTC
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

PAT_DD_DEV = re.compile(r"\bdd\s+[^;&|]*\bof=[\"']?/dev/")
PAT_DD_NO_OF = re.compile(r"\bdd\s+(?![^;&|]*\bof=)[^;&|]+")

_FALLBACK_CRITICAL_PATTERNS = (
    re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*f[a-zA-Z]*\s+(?:/(?:\s|$|\*)|~(?:\s|$|/\s|/\*|/$|\$)|(?:\$HOME|\$\{HOME\})(?:\s|$|/\s|/\*|/$|\$))"),
    re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*[rR][a-zA-Z]*\s+(?:/(?:\s|$|\*)|~(?:\s|$|/\s|/\*|/$|\$)|(?:\$HOME|\$\{HOME\})(?:\s|$|/\s|/\*|/$|\$))"),
    re.compile(r"\bdrop\s+(?:database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
    re.compile(r"\bmkfs(?:\.\w+)?\b"),
    PAT_DD_DEV,
    PAT_DD_NO_OF,
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


def _has_compound_shell_operators(cmd_clean: str) -> bool:
    return any(tok in cmd_clean for tok in ("&&", "||", ";", "$(", "`", "\n", ">"))


def _has_deletion_command(cmd_clean: str) -> bool:
    return bool(
        re.search(r"\brm(?:\s+|$)", cmd_clean)
        or re.search(r"\bgit\s+worktree\s+remove\b", cmd_clean)
        or re.search(r"\bgit\s+clean\b", cmd_clean)
        or re.search(r"\bgit\s+branch\s+-[dD]\b", cmd_clean)
        or re.search(r"\bunlink\b", cmd_clean)
        or re.search(r"\brmdir\b", cmd_clean)
    )


def _log_guard_event(
    command: str,
    risk_level: str,
    confidence: float,
    source: str,
    cwd: str,
    block_type: Optional[str] = None,
) -> None:
    try:
        log_dir = Path.home() / ".hermes" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "jev_guard_events.jsonl"
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "command": command[:1000],
            "risk_level": risk_level,
            "confidence": confidence,
            "source": source,
            "cwd": cwd,
            "block_type": block_type,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def _fallback_check_command_safety(command: str) -> dict[str, Any]:
    cmd = (command or "").strip()
    for pat in _FALLBACK_CRITICAL_PATTERNS:
        if pat.search(cmd):
            if pat in (PAT_DD_DEV, PAT_DD_NO_OF):
                block_type = "block_device_write"
                reason = "Writing directly to raw block device (/dev/) or unredirected dd is prohibited"
            else:
                block_type = "protected_path"
                reason = "Command matches critical destruction rule on protected system resource"

            return {
                "allowed": False,
                "risk_level": "critical_destruction",
                "is_destructive_confidence": 1.0,
                "source": "deterministic_rule",
                "reason": reason,
                "requires_human": True,
                "block_type": block_type,
            }
    if _has_compound_shell_operators(cmd) and _has_deletion_command(cmd):
        return {
            "allowed": False,
            "risk_level": "critical_destruction",
            "is_destructive_confidence": 0.85,
            "source": "deterministic_rule",
            "reason": "Chained deletion command detected. Split the deletion into an independent command instead of chaining with verification or build commands.",
            "requires_human": False,
            "block_type": "chained_delete",
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

    effective_cwd = workdir or cwd
    if guard_mod is not None and hasattr(guard_mod, "check_command_safety"):
        try:
            verdict = guard_mod.check_command_safety(
                command,
                context={
                    "cwd": effective_cwd,
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
    block_type = verdict.get("block_type")
    if not block_type:
        if "chained" in reason.lower() or "chained" in risk_level.lower():
            block_type = "chained_delete"
        elif "block device" in reason.lower() or "/dev/" in command:
            block_type = "block_device_write"
        else:
            block_type = "protected_path"

    requires_human = bool(verdict.get("requires_human", block_type != "chained_delete"))

    if block_type == "chained_delete":
        action_msg = "Do not halt the card. Split the chained deletion into an independent command so verification and cleanup execute separately."
    elif block_type == "block_device_write":
        action_msg = "Writing to raw block device (/dev/). Change output target to a temporary file in /tmp or route destructive actions to human approval."
    else:
        action_msg = "Protected path or destructive operation. Route destructive actions to human approval."

    error_text = f"Blocked by Jev automode safety guard ({risk_level}): {reason}. {action_msg}"

    _log_guard_event(
        command,
        risk_level,
        float(verdict.get("is_destructive_confidence", 1.0)),
        verdict.get("source", "jev_automode_guard"),
        effective_cwd,
        block_type,
    )

    logger.warning(
        "Blocked terminal command via Jev automode guard (risk=%s, type=%s, source=%s): %s",
        risk_level,
        block_type,
        verdict.get("source", "unknown"),
        reason,
    )
    return json.dumps(
        {
            "output": "",
            "exit_code": 1,
            "error": error_text,
            "status": "blocked",
            "requires_human": requires_human,
            "risk_level": risk_level,
            "block_type": block_type,
            "refusal_code": block_type,
            "source": verdict.get("source", "jev_automode_guard"),
        },
        ensure_ascii=False,
    )


__all__ = ["jev_automode_block", "jev_guard_enabled"]
