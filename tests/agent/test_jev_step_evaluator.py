"""Tests for Jev step evaluator integration inside turn_tool_round and kanban_stop."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import agent.jev_step_evaluator as jse
from agent.kanban_stop import build_kanban_stop_nudge
from agent.turn_tool_round import run_tool_round


def _tool_call(call_id: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _dummy_agent(tool_outputs: dict[str, str]):
    ns = SimpleNamespace(
        quiet_mode=True,
        verbose_logging=False,
        log_prefix="",
        valid_tool_names=set(tool_outputs) | {"terminal", "read_file", "kanban_complete", "kanban_block"},
        session_id=None,
        platform="cli",
        model="test/model",
        stream_delta_callback=None,
        _tool_guardrail_halt_decision=None,
        _last_content_with_tools=None,
        _last_content_tools_all_housekeeping=False,
        _mute_post_response=False,
        _post_tool_empty_retried=False,
        _dropped_toolcall_retries=0,
        _last_jev_step_verdict=None,
        _jev_needs_human_steered=False,
        _invalid_tool_retries=0,
        _invalid_json_retries=0,
        compression_enabled=False,
        tools=[],
        context_compressor=SimpleNamespace(
            awaiting_real_usage_after_compression=False,
            last_prompt_tokens=1,
            should_compress=lambda _t: False,
        ),
        _session_messages=[],
        iteration_budget=SimpleNamespace(refund=lambda: None),
        _vprint=lambda *_a, **_k: None,
        _buffer_vprint=lambda *_a, **_k: None,
        _uniquify_tool_call_ids=lambda tcs, taken=None: None,
        _repair_tool_call=lambda _name: None,
        _deduplicate_tool_calls=lambda tcs: tcs,
        _cap_delegate_task_calls=lambda tcs: tcs,
        _build_assistant_message=lambda msg, finish_reason: {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        },
        _has_content_after_think_block=lambda _c: False,
        _strip_think_blocks=lambda c: c,
        _has_stream_consumers=lambda: False,
        _should_emit_quiet_tool_messages=lambda: False,
        _interim_assistant_visible_text=lambda _m: "",
        _flush_messages_to_session_db=lambda *_a, **_k: True,
        _emit_interim_assistant_message=lambda *_a, **_k: None,
        _emit_diagnostic_status=lambda *_a, **_k: None,
        _touch_activity=lambda *_a, **_k: None,
    )

    def _exec(assistant_message, messages, effective_task_id, api_call_count):
        for tc in assistant_message.tool_calls:
            messages.append({
                "role": "tool",
                "name": tc.function.name,
                "tool_call_id": tc.id,
                "content": tool_outputs[tc.id],
            })

    ns._execute_tool_calls = _exec
    return ns


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, hermes_bootstrap_isolated_manifest):
    for key in (
        "HERMES_JEV_STEP_EVALUATOR",
        "HERMES_JEV_STEP_EVALUATOR_NETWORK",
        "HERMES_KANBAN_TASK",
    ):
        monkeypatch.delenv(key, raising=False)


def _run_round(agent, tc, user_message="Run tests"):
    assistant_msg = SimpleNamespace(content="", tool_calls=[tc], reasoning_details=None)
    messages = [{"role": "user", "content": user_message}]
    return run_tool_round(
        agent,
        assistant_message=assistant_msg,
        finish_reason="tool_calls",
        messages=messages,
        conversation_history=[],
        api_call_count=1,
        effective_task_id="task-1",
        user_message=user_message,
        system_message="sys",
        active_system_prompt="sys",
        compression_attempts=0,
        max_compression_attempts=3,
        final_response="",
        failed=False,
        _turn_exit_reason="unknown",
        truncated_tool_call_retries=0,
        current_turn_user_idx=0,
    )


def test_step_evaluator_stops_non_kanban_loop_on_clean_test_pass(monkeypatch):
    monkeypatch.setenv("HERMES_JEV_STEP_EVALUATOR", "1")
    tc = _tool_call("c1", "terminal", {"command": "pytest -q"})
    agent = _dummy_agent({"c1": json.dumps({"output": "28 passed in 7.25s", "exit_code": 0})})

    verdict = _run_round(agent, tc)
    assert verdict.action == "break"
    assert verdict._turn_exit_reason == "jev_step_evaluator_stop"
    assert "Completed" in verdict.final_response
    assert agent._last_jev_step_verdict["action"] == "stop_success"


def test_step_evaluator_stops_non_kanban_loop_on_fatal_error(monkeypatch):
    monkeypatch.setenv("HERMES_JEV_STEP_EVALUATOR", "1")
    tc = _tool_call("c1", "terminal", {"command": "git push origin main"})
    agent = _dummy_agent({"c1": json.dumps({"output": "fatal: Authentication failed for repo", "exit_code": 128})})

    verdict = _run_round(agent, tc)
    assert verdict.action == "break"
    assert verdict._turn_exit_reason == "jev_step_evaluator_needs_human"
    assert agent._last_jev_step_verdict["needs_human"] is True


def test_step_evaluator_ignores_passive_read_with_fatal_substring(monkeypatch):
    monkeypatch.setenv("HERMES_JEV_STEP_EVALUATOR", "1")
    tc_cat = _tool_call("c1", "terminal", {"command": "cat auth.log"})
    agent = _dummy_agent({"c1": json.dumps({"output": "Authentication failed line in fixture", "exit_code": 0})})
    verdict = _run_round(agent, tc_cat)
    assert verdict.action == "continue"

    tc_echo = _tool_call("c2", "terminal", {"command": "python3 print_fixture.py"})
    agent2 = _dummy_agent({"c2": json.dumps({"output": "Authentication failed", "exit_code": 0})})
    verdict2 = _run_round(agent2, tc_echo)
    assert verdict2.action == "continue"


def test_step_evaluator_respects_confidence_threshold_on_jev_layer2(monkeypatch):
    monkeypatch.setenv("HERMES_JEV_STEP_EVALUATOR", "1")

    low_eval = SimpleNamespace(
        evaluate_step=lambda *_a, **_k: {
            "action": "stop_success",
            "is_finished": False,
            "has_error": False,
            "needs_human": False,
            "confidence": 0.72,
            "source": "jev_engine",
            "reason": "below 0.90",
        }
    )
    monkeypatch.setattr(jse, "_load_loop_evaluator", lambda: low_eval)
    tc = _tool_call("c1", "terminal", {"command": "./deploy.sh"})
    agent = _dummy_agent({"c1": json.dumps({"output": "Deploying...", "exit_code": 0})})
    assert _run_round(agent, tc).action == "continue"

    high_eval = SimpleNamespace(
        evaluate_step=lambda *_a, **_k: {
            "action": "stop_success",
            "is_finished": True,
            "has_error": False,
            "needs_human": False,
            "confidence": 0.95,
            "source": "jev_engine",
            "reason": "Deployment verified",
        }
    )
    monkeypatch.setattr(jse, "_load_loop_evaluator", lambda: high_eval)
    verdict = _run_round(_dummy_agent({"c2": json.dumps({"output": "ok", "exit_code": 0})}), _tool_call("c2", "terminal", {"command": "./verify.sh"}))
    assert verdict.action == "break"
    assert verdict._turn_exit_reason == "jev_step_evaluator_stop"


def test_kanban_worker_steers_instead_of_breaking_and_embeds_nudge(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_kanban_jev")
    tc = _tool_call("c1", "terminal", {"command": "rm -rf /"})
    agent = _dummy_agent({
        "c1": json.dumps({
            "output": "",
            "exit_code": 1,
            "status": "blocked",
            "requires_human": True,
            "error": "Blocked by Jev automode safety guard (critical_destruction)",
        })
    })

    verdict = _run_round(agent, tc, user_message="Clean workspace")
    assert verdict.action == "continue"
    assert agent._last_jev_step_verdict["action"] == "needs_human"
    assert verdict.messages[-1]["role"] == "user"
    assert "kanban_block" in verdict.messages[-1]["content"]

    nudge = build_kanban_stop_nudge(
        messages=verdict.messages,
        attempts=0,
        step_verdict=agent._last_jev_step_verdict,
    )
    assert nudge is not None
    assert "needs_human" in nudge
    assert "kanban_block" in nudge


def test_step_evaluator_loads_script_from_hermes_home(monkeypatch, tmp_path):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    (script_dir / "jev_loop_evaluator.py").write_text(
        "def evaluate_step(output, task_description='', context=None, completion_threshold=0.85, allow_network=None):\n"
        "    return {\n"
        "        'action': 'stop_success',\n"
        "        'is_finished': True,\n"
        "        'has_error': False,\n"
        "        'needs_human': False,\n"
        "        'confidence': 0.96,\n"
        "        'source': 'hermes_home_script',\n"
        "        'reason': 'custom script stop',\n"
        "    }\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_JEV_STEP_EVALUATOR", "1")
    tc = _tool_call("c1", "terminal", {"command": "./ship.sh"})
    agent = _dummy_agent({"c1": json.dumps({"output": "shipped", "exit_code": 0})})
    verdict = _run_round(agent, tc, user_message="Ship release")
    assert verdict.action == "break"
    assert agent._last_jev_step_verdict["source"] == "hermes_home_script"

