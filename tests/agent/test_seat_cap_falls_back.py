"""A subscription seat's plan cap (429 with a long Retry-After) must move the turn to the next
fallback lane even when the credential pool holds more than one entry. Sleeping the 600 s cap on
the same seat stranded an attended turn until the gateway gave up ("Your request was not
processed"). A short Retry-After still waits on pool rotation (#11314)."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from agent.error_classifier import classify_api_error
from agent.turn_recovery import TurnRetryState, route_classified_error

BASE = "https://api.anthropic.com"

pytestmark = pytest.mark.usefixtures("hermes_bootstrap_isolated_manifest")
MODEL = "claude-opus-5-5"


def _err(retry_after: str):
    req = httpx.Request("POST", f"{BASE}/v1/messages")
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": "This request would exceed your rate limit"}}
    resp = httpx.Response(429, json=body, headers={"retry-after": retry_after}, request=req)
    return openai.RateLimitError("This request would exceed your rate limit", response=resp, body=body)


def _agent(fallback_ok: list):
    pool = MagicMock()
    pool.entries.return_value = [MagicMock(), MagicMock()]
    pool.has_available.return_value = True
    noop = lambda *a, **kw: None

    def _activate(**kw):
        fallback_ok.append(kw)
        return True

    return SimpleNamespace(
        provider="anthropic", model=MODEL, base_url=BASE, log_prefix="", compression_enabled=True,
        _credential_pool=pool, _fallback_index=0, _fallback_chain=[{"provider": "openai-codex", "model": "gpt-6-luna"}],
        _buffer_diagnostic_status=noop, _try_activate_fallback=_activate, _emit_diagnostic_status=noop,
    )


def _route(agent, err):
    classified = classify_api_error(err, provider="anthropic", model=MODEL, base_url=BASE)
    with patch("agent.conversation_loop._arm_fallback_restart", lambda a, m, s, r: s):
        return route_classified_error(
            agent, err, classified, TurnRetryState(), error_msg=str(err), error_context={},
            recovered_with_pool=False, base_url=BASE, model=MODEL, messages=[], api_messages=[],
            system_message="", active_system_prompt="", conversation_history=[], retry_count=0,
            max_retries=3, compression_attempts=0, max_compression_attempts=3, api_call_count=1,
            effective_task_id=None,
        )


def test_long_retry_after_on_multi_seat_pool_falls_back():
    calls: list = []
    verdict = _route(_agent(calls), _err("600"))
    assert calls, "plan-cap 429 should activate the next fallback lane"
    assert verdict.action == "break"


@pytest.mark.parametrize("retry_after", ["5", "30"])
def test_short_retry_after_still_waits_on_pool_rotation(retry_after):
    calls: list = []
    _route(_agent(calls), _err(retry_after))
    assert calls == []
