"""Jev step-level completion and blocker evaluator for autonomous agent loops.

Evaluates substantive tool execution results via ``~/.hermes/scripts/jev_loop_evaluator.py``
(using the ``agent_loop_done_check`` preset) so fatal external blockers or cleanly
verified completion states are caught without burning extra frontier LLM turns.
Reads only tool-round results and preserves prompt-caching and role-alternation
invariants.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.delegation_context import owned_kanban_task
from agent.kanban_stop import session_called_kanban_terminal
from agent.message_sanitization import coalesce_tool_call_id

logger = logging.getLogger("agent.jev_step_evaluator")

_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "disabled"})
_DEFAULT_MIN_CONFIDENCE = 0.90

_FALLBACK_PASS_PATTERNS = (
    re.compile(r"=*\s*\d+\s+passed(?:,\s*\d+\s+warnings?)?\s+in\s+[\d\.]+s\s*=*", re.IGNORECASE),
    re.compile(r"\bRan\s+\d+\s+tests?\s+in\s+[\d\.]+s\s+OK\b", re.DOTALL | re.IGNORECASE),
    re.compile(r"\bbuild\s+succeeded\b", re.IGNORECASE),
    re.compile(r"\ball\s+tests\s+passed\b", re.IGNORECASE),
)

_FALLBACK_FATAL_PATTERNS = (
    re.compile(r"\bAuthentication failed\b", re.IGNORECASE),
    re.compile(r"\bPermission denied \(publickey\)\b", re.IGNORECASE),
    re.compile(r"\bInvalid credentials\b", re.IGNORECASE),
    re.compile(r"\bQuota exceeded\b", re.IGNORECASE),
)


def _in_isolated_test() -> bool:
    return bool(os.environ.get("HERMES_TEST_ISOLATION") or os.environ.get("PYTEST_CURRENT_TEST"))

_READ_ONLY_INSPECTION_TOOLS = frozenset({
    "read_file",
    "search_files",
    "list_dir",
    "memory",
    "todo",
    "todo_list",
    "skill_view",
    "skills_list",
    "session_search",
    "kanban_show",
    "kanban_list",
    "kanban_heartbeat",
    "kanban_comment",
})

_PASSIVE_TERMINAL_PREFIXES = frozenset({
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "rg",
    "find",
    "ls",
    "git log",
    "git show",
    "git diff",
    "git status",
})


@dataclass
class JevStepAssessment:
    """Outcome of post-tool step evaluation."""

    action: str = "continue"  # "continue" | "break"
    verdict: Optional[dict[str, Any]] = None
    final_response: Optional[str] = None
    exit_reason: Optional[str] = None
    steer_message: Optional[str] = None


def jev_step_evaluator_enabled() -> bool:
    """Enabled automatically for Kanban workers or when ``HERMES_JEV_STEP_EVALUATOR=1``."""
    raw = (os.environ.get("HERMES_JEV_STEP_EVALUATOR") or "").strip().lower()
    if raw in _FALSE_WORDS:
        return False
    if raw in _TRUE_WORDS:
        return True
    return bool(owned_kanban_task())


def _network_allowed() -> bool:
    raw = (os.environ.get("HERMES_JEV_STEP_EVALUATOR_NETWORK") or "").strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def _resolve_evaluator_script_path() -> Optional[Path]:
    override = (os.environ.get("HERMES_JEV_LOOP_EVALUATOR_PATH") or "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    try:
        from hermes_constants import get_hermes_home

        candidates.append(Path(get_hermes_home()) / "scripts" / "jev_loop_evaluator.py")
    except Exception:
        pass
    if not _in_isolated_test():
        candidates.append(Path.home() / ".hermes" / "scripts" / "jev_loop_evaluator.py")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _fallback_evaluate_step(output: str) -> dict[str, Any]:
    out_clean = (output or "").strip()
    for pat in _FALLBACK_FATAL_PATTERNS:
        if pat.search(out_clean):
            return {
                "action": "needs_human",
                "is_finished": False,
                "has_error": True,
                "needs_human": True,
                "confidence": 1.0,
                "source": "deterministic_rule",
                "reason": f"Output matched fatal credential pattern: {pat.pattern}",
            }
    for pat in _FALLBACK_PASS_PATTERNS:
        if pat.search(out_clean) and "FAILED" not in out_clean and "ERROR" not in out_clean:
            return {
                "action": "stop_success",
                "is_finished": True,
                "has_error": False,
                "needs_human": False,
                "confidence": 1.0,
                "source": "deterministic_rule",
                "reason": "Output matched deterministic success test pattern",
            }
    return {
        "action": "continue",
        "is_finished": False,
        "has_error": False,
        "needs_human": False,
        "confidence": 0.5,
        "source": "fallback",
        "reason": "External jev_loop_evaluator script unavailable; fallback continue",
    }


def _load_loop_evaluator() -> Any:
    script_path = _resolve_evaluator_script_path()
    if script_path is None:
        return None
    spec = importlib.util.spec_from_file_location("hermes_jev_loop_evaluator", script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _is_passive_terminal_read(command: str) -> bool:
    cmd = (command or "").strip().lower()
    if not cmd:
        return False
    for prefix in _PASSIVE_TERMINAL_PREFIXES:
        if cmd == prefix or cmd.startswith(prefix + " "):
            return True
    return False


def _collect_round_outputs(
    assistant_message: Any, messages: list[dict[str, Any]]
) -> tuple[Optional[dict[str, Any]], list[str], bool]:
    """Return ``(immediate_human_block, candidate_output_snippets, has_failure_signal)``."""
    tool_calls = getattr(assistant_message, "tool_calls", None) or []
    if not tool_calls:
        return None, [], False

    call_meta: dict[str, tuple[str, dict[str, Any]]] = {}
    for tc in tool_calls:
        call_id = coalesce_tool_call_id(tc)
        fn = getattr(tc, "function", None)
        name = getattr(fn, "name", "") if fn is not None else ""
        args = _parse_tool_args(getattr(fn, "arguments", None) if fn is not None else None)
        if call_id:
            call_meta[call_id] = (name, args)

    tool_msgs: list[dict[str, Any]] = []
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            break
        if str(msg.get("tool_call_id") or "") in call_meta:
            tool_msgs.append(msg)
    tool_msgs.reverse()

    snippets: list[str] = []
    has_failure_signal = False
    for msg in tool_msgs:
        call_id = str(msg.get("tool_call_id") or "")
        tool_name, args = call_meta.get(call_id, (str(msg.get("name") or ""), {}))
        if tool_name in _READ_ONLY_INSPECTION_TOOLS:
            continue
        raw_content = msg.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            continue

        parsed = None
        try:
            candidate = json.loads(raw_content)
            if isinstance(candidate, dict):
                parsed = candidate
        except Exception:
            parsed = None

        if parsed is not None:
            if parsed.get("requires_human") is True and parsed.get("status") == "blocked":
                return {
                    "action": "needs_human",
                    "is_finished": False,
                    "has_error": True,
                    "needs_human": True,
                    "confidence": 1.0,
                    "source": str(parsed.get("source") or "jev_automode_guard"),
                    "reason": str(parsed.get("error") or "Command blocked requiring human approval"),
                }, [], True

            if tool_name in ("terminal", "execute_code", "process", "process_manage"):
                exit_code = parsed.get("exit_code")
                status = str(parsed.get("status") or "").lower()
                err_text = str(parsed.get("error") or "").strip()
                out_text = str(parsed.get("output") or "").strip()
                cmd = str(args.get("command") or "")
                is_clean_zero = (
                    (exit_code in (None, 0))
                    and status not in ("error", "blocked", "degraded")
                    and not err_text
                )
                if not is_clean_zero:
                    has_failure_signal = True
                if is_clean_zero and _is_passive_terminal_read(cmd):
                    continue
                combined = "\n".join(p for p in (out_text, err_text) if p).strip()
                if combined:
                    snippets.append(combined)
                continue

        has_failure_signal = True
        snippets.append(raw_content.strip())

    return None, snippets, has_failure_signal


def assess_tool_round(
    agent: Any,
    *,
    assistant_message: Any,
    messages: list[dict[str, Any]],
    user_message: Any,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
) -> JevStepAssessment:
    """Assess the finished tool round and return whether to stop early or continue."""
    if not jev_step_evaluator_enabled():
        return JevStepAssessment()

    owned_tid = owned_kanban_task()
    if owned_tid and session_called_kanban_terminal(messages, owned_tid):
        return JevStepAssessment()

    verdict, snippets, has_failure_signal = _collect_round_outputs(assistant_message, messages)
    if verdict is None:
        if not snippets:
            return JevStepAssessment()
        try:
            evaluator = _load_loop_evaluator()
        except Exception as exc:
            logger.warning("Failed to load jev_loop_evaluator: %s", exc)
            evaluator = None

        joined = "\n\n".join(snippets)[:4000]
        if evaluator is not None and hasattr(evaluator, "evaluate_step"):
            task_text = user_message if isinstance(user_message, str) else ""
            try:
                verdict = evaluator.evaluate_step(
                    joined,
                    task_description=task_text[:1000],
                    context={"agent": "hermes-kanban" if owned_tid else "hermes", "task_id": owned_tid},
                    completion_threshold=min_confidence,
                    allow_network=_network_allowed(),
                )
            except Exception as exc:
                logger.warning("jev_loop_evaluator check failed: %s", exc)
                verdict = _fallback_evaluate_step(joined)
        else:
            verdict = _fallback_evaluate_step(joined)

    if not isinstance(verdict, dict):
        return JevStepAssessment()

    if (
        verdict.get("action") == "needs_human"
        and verdict.get("source") == "deterministic_rule"
        and not has_failure_signal
    ):
        return JevStepAssessment(action="continue")

    agent._last_jev_step_verdict = verdict
    action = verdict.get("action")
    confidence = float(verdict.get("confidence", 0.0) or 0.0)
    if confidence < min_confidence or action not in ("stop_success", "needs_human"):
        return JevStepAssessment(action="continue", verdict=verdict)

    reason = str(verdict.get("reason") or f"Jev evaluator action={action}")
    if owned_tid:
        if action == "needs_human" and not getattr(agent, "_jev_needs_human_steered", False):
            agent._jev_needs_human_steered = True
            return JevStepAssessment(
                action="continue",
                verdict=verdict,
                steer_message=(
                    f"[System: Jev step evaluator flagged a human blocker on task `{owned_tid}` "
                    f"(confidence={confidence:.2f}, reason={reason}). Stop retrying automatically and "
                    "call `kanban_block(reason=...)` immediately so a human can resolve it.]"
                ),
            )
        return JevStepAssessment(action="continue", verdict=verdict)

    if action == "needs_human":
        return JevStepAssessment(
            action="break",
            verdict=verdict,
            exit_reason="jev_step_evaluator_needs_human",
            final_response=f"Stopped for human review (confidence {confidence:.2f}): {reason}",
        )

    return JevStepAssessment(
        action="break",
        verdict=verdict,
        exit_reason="jev_step_evaluator_stop",
        final_response=f"Completed (verified at confidence {confidence:.2f}): {reason}",
    )


__all__ = ["JevStepAssessment", "assess_tool_round", "jev_step_evaluator_enabled"]
