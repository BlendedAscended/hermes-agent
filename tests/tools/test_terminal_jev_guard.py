"""Tests for pre-execution Jev automode guard inside terminal_tool."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools import jev_guard
import tools.terminal_tool as terminal_module


def _plan(timeout: float = 5.0) -> SimpleNamespace:
    return SimpleNamespace(
        config={},
        env_type="local",
        effective_task_id="jev-guard-test",
        cwd="/tmp",
        host_cwd=None,
        effective_timeout=timeout,
        promoted_from_foreground_timeout=None,
    )


@pytest.fixture
def stubbed_terminal(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(terminal_module, "_PRE_EXEC_GUARD_MIN_TIMEOUT_S", 0)
    monkeypatch.setattr(terminal_module, "_plan_execution", lambda *_a, **_k: _plan())
    monkeypatch.setattr(terminal_module, "_acquire_env", lambda *_a, **_k: object())
    monkeypatch.setattr(
        terminal_module, "_run_approval_guards", lambda *_a, **_k: terminal_module._ApprovalVerdict(),
    )
    monkeypatch.setattr(
        terminal_module, "_run_foreground", lambda cmd, *_a, **_k: calls.append(cmd) or "foreground-ran",
    )
    monkeypatch.delenv("HERMES_JEV_GUARD", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    return calls


def test_jev_guard_blocks_destructive_command_even_with_force(monkeypatch, stubbed_terminal):
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")
    result = json.loads(terminal_module.terminal_tool("rm -rf /", force=True))
    assert result["status"] == "blocked"
    assert result["requires_human"] is True
    assert result["block_type"] == "protected_path"
    assert result["refusal_code"] == "protected_path"
    assert "Protected path or destructive operation" in result["error"]
    assert stubbed_terminal == []


def test_jev_guard_allows_safe_command(monkeypatch, stubbed_terminal):
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")
    out = terminal_module.terminal_tool("echo ok")
    assert out == "foreground-ran"
    assert stubbed_terminal == ["echo ok"]


def test_jev_guard_auto_enables_for_kanban_worker_and_honors_opt_out(monkeypatch, stubbed_terminal):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_kanban_1")
    result = json.loads(terminal_module.terminal_tool("DROP DATABASE prod;"))
    assert result["status"] == "blocked"
    assert stubbed_terminal == []

    monkeypatch.setenv("HERMES_JEV_GUARD", "0")
    out = terminal_module.terminal_tool("DROP DATABASE prod;")
    assert out == "foreground-ran"


def test_jev_guard_blocks_layer2_verdict_from_automode_module(monkeypatch, stubbed_terminal):
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")
    fake_mod = SimpleNamespace(
        check_command_safety=lambda cmd, **_kw: {
            "allowed": False,
            "risk_level": "critical_destruction",
            "is_destructive_confidence": 0.93,
            "source": "jev_engine",
            "reason": f"Jev flagged {cmd}",
            "requires_human": True,
            "block_type": "protected_path",
        }
    )
    monkeypatch.setattr(jev_guard, "_load_automode_guard", lambda: fake_mod)

    result = json.loads(terminal_module.terminal_tool("git push --force origin main"))
    assert result["status"] == "blocked"
    assert result["source"] == "jev_engine"
    assert result["requires_human"] is True
    assert result["block_type"] == "protected_path"
    assert stubbed_terminal == []


def test_jev_guard_distinguishes_chained_delete_from_protected_path(monkeypatch, stubbed_terminal):
    """Chained deletions guide workers to split commands (requires_human=False) instead of halting card."""
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")

    # Chained delete: worker can recover by splitting command
    result = json.loads(terminal_module.terminal_tool("npm test && rm -rf dist"))
    assert result["status"] == "blocked"
    assert result["block_type"] == "chained_delete"
    assert result["refusal_code"] == "chained_delete"
    assert result["requires_human"] is False
    assert "Do not halt the card" in result["error"]
    assert "Split the chained deletion" in result["error"]
    assert stubbed_terminal == []

    # Protected path: requires human approval
    prot = json.loads(terminal_module.terminal_tool("rm -rf ~"))
    assert prot["status"] == "blocked"
    assert prot["block_type"] == "protected_path"
    assert prot["refusal_code"] == "protected_path"
    assert prot["requires_human"] is True
    assert "Route destructive actions to human approval" in prot["error"]


def test_jev_guard_fallback_dd_scoped_to_dev(monkeypatch, stubbed_terminal):
    """Fallback critical patterns block dd only for /dev/ or missing of=, allowing /tmp fixtures."""
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")
    # Force fallback path by pointing guard loader to non-existent module
    monkeypatch.setattr(jev_guard, "_load_automode_guard", lambda: None)

    # Allowed: writing fixture to /tmp
    out = terminal_module.terminal_tool("dd if=/dev/urandom of=/tmp/fixture.bin bs=1M count=10")
    assert out == "foreground-ran"
    assert stubbed_terminal == ["dd if=/dev/urandom of=/tmp/fixture.bin bs=1M count=10"]

    stubbed_terminal.clear()

    # Blocked: writing to /dev/sda
    blocked_dev = json.loads(terminal_module.terminal_tool("dd if=/dev/zero of=/dev/sda"))
    assert blocked_dev["status"] == "blocked"
    assert blocked_dev["block_type"] == "block_device_write"
    assert blocked_dev["refusal_code"] == "block_device_write"
    assert blocked_dev["requires_human"] is True
    assert stubbed_terminal == []

    # Blocked: missing of=
    blocked_no_of = json.loads(terminal_module.terminal_tool("dd if=/dev/zero bs=512"))
    assert blocked_no_of["status"] == "blocked"
    assert blocked_no_of["block_type"] == "block_device_write"
    assert blocked_no_of["refusal_code"] == "block_device_write"
    assert blocked_no_of["requires_human"] is True


def test_jev_guard_resolves_custom_script_in_hermes_home(monkeypatch, stubbed_terminal, tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    script_path = script_dir / "jev_automode_guard.py"
    script_path.write_text(
        "def check_command_safety(command, context=None, destructive_threshold=0.85, allow_network=None):\n"
        "    if 'custom-danger' in command:\n"
        "        return {'allowed': False, 'risk_level': 'critical_destruction', 'source': 'hermes_home_script', 'reason': 'custom script block', 'requires_human': True, 'block_type': 'protected_path'}\n"
        "    return {'allowed': True, 'risk_level': 'read_only', 'source': 'hermes_home_script', 'reason': 'ok', 'requires_human': False}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_JEV_GUARD", "1")
    blocked = json.loads(terminal_module.terminal_tool("run custom-danger now"))
    assert blocked["status"] == "blocked"
    assert blocked["source"] == "hermes_home_script"
    assert terminal_module.terminal_tool("run safe") == "foreground-ran"
