"""Validated fallback policy for per-attempt Kanban runtime caps."""

from __future__ import annotations

from typing import Callable

DEFAULT_MAX_RUNTIME_SECONDS = 5400


class RuntimePolicyError(RuntimeError):
    """Configuration cannot safely determine the dispatcher runtime cap."""


def default_max_runtime_seconds(config_loader: Callable | None = None) -> int:
    """Resolve the NULL-task-cap fallback, failing closed on unreadable policy."""
    if config_loader is None:
        from hermes_cli.config import load_config_readonly

        config_loader = load_config_readonly
    try:
        config = config_loader()
    except Exception as exc:
        raise RuntimePolicyError("cannot read kanban runtime policy; dispatch tick refused") from exc
    if not isinstance(config, dict):
        raise RuntimePolicyError("kanban runtime policy is not a mapping; dispatch tick refused")
    kanban = config.get("kanban")
    if kanban is None:
        return DEFAULT_MAX_RUNTIME_SECONDS
    if not isinstance(kanban, dict):
        raise RuntimePolicyError("kanban runtime policy must be a mapping; dispatch tick refused")
    value = kanban.get("default_max_runtime_seconds", DEFAULT_MAX_RUNTIME_SECONDS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimePolicyError("kanban.default_max_runtime_seconds must be a nonnegative integer")
    return value
