"""Jev-gated reviewer model selection in the /review engine.

The gate is advisory: only a confident, decisive verdict may change routing, and any
failure leaves the static ``auxiliary.review`` pin (or parent inheritance) untouched.
"""
import json
import subprocess

import pytest

import agent.review_engine as re_mod


def _pick(reason="jev_confirmed", model="google/gemini-3.8-flash", confidence=0.9):
    return {"model": model, "reason": reason, "confidence": confidence, "margin_ratio": 2.0}


class TestApplyJevReviewModel:
    def test_accepted_pick_overrides_model_and_provider(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: _pick())
        static = {"provider": "openrouter", "model": "google/gemini-4-argon",
                  "base_url": "", "api_key": "", "api_mode": ""}
        cfg, pick = re_mod._apply_jev_review_model(static, "goal", "ctx")
        assert cfg["model"] == "google/gemini-3.8-flash"
        assert cfg["provider"] == "openrouter"
        assert pick["reason"] == "jev_confirmed"

    def test_static_pin_is_not_mutated(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: _pick())
        static = {"provider": "custom", "model": "moonshotai/kimi-k3.1-pro",
                  "base_url": "https://example.invalid/v1", "api_key": "secret", "api_mode": "chat"}
        re_mod._apply_jev_review_model(static, "goal", "ctx")
        assert static["model"] == "moonshotai/kimi-k3.1-pro"
        assert static["api_key"] == "secret"

    def test_inherited_endpoint_and_key_are_cleared(self, monkeypatch):
        """A picked OpenRouter model must never be sent to the parent's native endpoint."""
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: _pick())
        static = {"provider": "custom", "model": "moonshotai/kimi-k3.1-pro",
                  "base_url": "https://example.invalid/v1", "api_key": "secret", "api_mode": "chat"}
        cfg, _ = re_mod._apply_jev_review_model(static, "goal", "ctx")
        assert cfg["base_url"] == ""
        assert cfg["api_key"] == ""
        assert cfg["api_mode"] == ""

    def test_no_static_pin_builds_openrouter_cfg(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: _pick())
        cfg, pick = re_mod._apply_jev_review_model(None, "goal", "ctx")
        assert cfg["provider"] == "openrouter"
        assert cfg["model"] == "google/gemini-3.8-flash"
        assert pick is not None

    def test_thin_margin_escalation_is_honoured(self, monkeypatch):
        monkeypatch.setattr(
            re_mod, "_jev_review_pick",
            lambda g, c: _pick(reason="thin_margin_escalated", model="google/gemini-4-argon"),
        )
        cfg, pick = re_mod._apply_jev_review_model(None, "goal", "ctx")
        assert cfg["model"] == "google/gemini-4-argon"
        assert pick["reason"] == "thin_margin_escalated"

    @pytest.mark.parametrize("reason", [
        "below_confidence_floor_default", "packet_not_reviewable", "decision_error_default",
        "empty_answers_default", "unknown_choice_default",
    ])
    def test_unsure_verdicts_keep_the_static_pin(self, monkeypatch, reason):
        """An unsure gate can never cheapen a review by swapping in the cheap default."""
        monkeypatch.setattr(
            re_mod, "_jev_review_pick",
            lambda g, c: _pick(reason=reason, model="deepseek/deepseek-v4.1-flash", confidence=0.2),
        )
        static = {"provider": "openrouter", "model": "google/gemini-4-argon"}
        cfg, pick = re_mod._apply_jev_review_model(static, "goal", "ctx")
        assert cfg is static
        assert cfg["model"] == "google/gemini-4-argon"
        assert pick is None

    def test_gate_unavailable_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: None)
        cfg, pick = re_mod._apply_jev_review_model(None, "goal", "ctx")
        assert cfg is None
        assert pick is None


class TestJevReviewPickSubprocess:
    def test_missing_script_returns_none(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: False)
        assert re_mod._jev_review_pick("goal", "ctx") is None

    def test_timeout_returns_none_and_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: True)
        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="jev", timeout=25)
        monkeypatch.setattr(re_mod.subprocess, "run", boom)
        assert re_mod._jev_review_pick("goal", "ctx") is None

    def test_nonzero_exit_returns_none(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: True)
        monkeypatch.setattr(re_mod.subprocess, "run",
                            lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "boom"))
        assert re_mod._jev_review_pick("goal", "ctx") is None

    def test_garbage_stdout_returns_none(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: True)
        monkeypatch.setattr(re_mod.subprocess, "run",
                            lambda *a, **kw: subprocess.CompletedProcess(a, 0, "not json", ""))
        assert re_mod._jev_review_pick("goal", "ctx") is None

    def test_valid_stdout_is_parsed(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: True)
        monkeypatch.setattr(
            re_mod.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(_pick()), ""),
        )
        pick = re_mod._jev_review_pick("goal", "ctx")
        assert pick["model"] == "google/gemini-3.8-flash"

    def test_empty_model_is_rejected(self, monkeypatch):
        monkeypatch.setattr(re_mod.os.path, "exists", lambda p: True)
        monkeypatch.setattr(
            re_mod.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(_pick(model="")), ""),
        )
        assert re_mod._jev_review_pick("goal", "ctx") is None


class TestStartReviewMetadata:
    def test_dispatch_carries_the_pick_and_model(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_load_review_credentials_cfg", lambda: None)
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: _pick())
        seen = {}
        import tools.delegate_tool as dt
        def fake_delegate_task(**kwargs):
            seen.update(kwargs)
            return json.dumps({"status": "dispatched", "delegation_id": "d1"})
        monkeypatch.setattr(dt, "delegate_task", fake_delegate_task)
        result = re_mod.start_review(object(), [{"role": "user", "content": "review this diff"}])
        assert result["review_model"] == "google/gemini-3.8-flash"
        assert result["review_model_pick"]["reason"] == "jev_confirmed"
        assert seen["credentials_cfg"]["provider"] == "openrouter"

    def test_dispatch_without_a_pick_has_no_pick_metadata(self, monkeypatch):
        monkeypatch.setattr(re_mod, "_load_review_credentials_cfg", lambda: None)
        monkeypatch.setattr(re_mod, "_jev_review_pick", lambda g, c: None)
        import tools.delegate_tool as dt
        monkeypatch.setattr(dt, "delegate_task",
                            lambda **kw: json.dumps({"status": "dispatched", "delegation_id": "d1"}))
        result = re_mod.start_review(object(), [{"role": "user", "content": "review this"}])
        assert "review_model_pick" not in result


class TestOverrideReasonVocabulary:
    """The engine only honours ``jev_confirmed`` and ``thin_margin_escalated``.

    The wrapper's own suite (~/.hermes/scripts/test_review_lane_pick.py) pins the
    same vocabulary from the emitting side; repo tests cannot read the real script
    because tests/home_io_guard.py refuses file I/O against the live hermes home.
    """

    def test_override_reasons_match_the_wrapper_contract(self):
        assert re_mod._JEV_OVERRIDE_REASONS == {"jev_confirmed", "thin_margin_escalated"}
