"""Tests for Arcee Trinity Large Thinking per-model overrides and compression policy.

Arcee Trinity Large Thinking is a reasoning model that wants:
- Fixed temperature=0.5 (vs the global default)
- Compression threshold=0.75 (delay compression to preserve reasoning context)

The helpers must match the bare model name, including when it arrives via
OpenRouter as ``arcee-ai/trinity-large-thinking``, but must NOT hit sibling
Arcee models like trinity-large-preview or trinity-mini.

Compression threshold policy was moved from auxiliary_client.py to
context_compressor.py.  The functions now live there as the single source
of truth.
"""

from __future__ import annotations

import pytest

from agent.auxiliary_client import (
    _fixed_temperature_for_model,
    _is_arcee_trinity_thinking,
)
from agent.context_compressor import (
    compression_threshold_for_model,
    compression_threshold_for_context_length,
    resolve_compression_threshold,
)
from agent.model_metadata import MINIMUM_CONTEXT_LENGTH


@pytest.mark.parametrize(
    "model",
    [
        "trinity-large-thinking",
        "arcee-ai/trinity-large-thinking",
        "Arcee-AI/Trinity-Large-Thinking",  # case-insensitive
        "  trinity-large-thinking  ",  # whitespace tolerant
    ],
)
def test_is_arcee_trinity_thinking_matches(model: str) -> None:
    assert _is_arcee_trinity_thinking(model) is True


@pytest.mark.parametrize(
    "model",
    [
        None,
        "",
        "trinity-large-preview",
        "arcee-ai/trinity-large-preview:free",
        "trinity-mini",
        "arcee-ai/trinity-mini",
        "trinity-large",  # prefix-only must not match
        "claude-sonnet-4.6",
        "gpt-5.4",
    ],
)
def test_is_arcee_trinity_thinking_rejects_non_matches(model) -> None:
    assert _is_arcee_trinity_thinking(model) is False


def test_fixed_temperature_for_trinity_thinking() -> None:
    assert _fixed_temperature_for_model("trinity-large-thinking") == 0.5
    assert _fixed_temperature_for_model("arcee-ai/trinity-large-thinking") == 0.5


def test_fixed_temperature_sibling_arcee_models_unaffected() -> None:
    # Preview and mini do not pin temperature — caller chooses its default.
    assert _fixed_temperature_for_model("trinity-large-preview") is None
    assert _fixed_temperature_for_model("trinity-mini") is None


# ── Compression threshold policy (now in context_compressor.py) ──────────


def test_compression_threshold_for_trinity_thinking() -> None:
    assert compression_threshold_for_model("trinity-large-thinking") == 0.75
    assert compression_threshold_for_model("arcee-ai/trinity-large-thinking") == 0.75


def test_compression_threshold_default_none_for_other_models() -> None:
    # None means "leave the adaptive tier to decide".
    assert compression_threshold_for_model(None) is None
    assert compression_threshold_for_model("") is None
    assert compression_threshold_for_model("trinity-large-preview") is None
    assert compression_threshold_for_model("claude-sonnet-4.6") is None
    assert compression_threshold_for_model("kimi-k2") is None


def test_adaptive_tier_breakpoints() -> None:
    """Verify the adaptive tier thresholds and that the first tier uses MINIMUM_CONTEXT_LENGTH."""
    # First tier derives from the constant, not hardcoded 32K
    assert compression_threshold_for_context_length(MINIMUM_CONTEXT_LENGTH) == 0.60
    assert compression_threshold_for_context_length(0) == 0.50  # unknown model
    assert compression_threshold_for_context_length(32_000) == 0.60
    assert compression_threshold_for_context_length(64_000) == 0.70
    assert compression_threshold_for_context_length(128_000) == 0.85
    assert compression_threshold_for_context_length(256_000) == 0.90
    assert compression_threshold_for_context_length(1_000_000) == 0.95
    assert compression_threshold_for_context_length(1_500_000) == 0.96  # >1M


def test_adaptive_tier_first_tier_coupled_to_minimum() -> None:
    """If MINIMUM_CONTEXT_LENGTH changes, the first tier boundary follows."""
    # Values just at and just below MINIMUM_CONTEXT_LENGTH
    assert compression_threshold_for_context_length(MINIMUM_CONTEXT_LENGTH - 1) == 0.60
    assert compression_threshold_for_context_length(MINIMUM_CONTEXT_LENGTH) == 0.60
    # The tier boundary IS MINIMUM_CONTEXT_LENGTH (not hardcoded 32K)
    assert compression_threshold_for_context_length(MINIMUM_CONTEXT_LENGTH + 1) <= 0.70


def test_resolve_compression_threshold_model_override_wins() -> None:
    """Per-model override takes priority over adaptive tier and config."""
    # Arcee Trinity (256K context) should get 0.75 from model override,
    # NOT 0.90 from the adaptive tier for 256K context.
    result = resolve_compression_threshold(
        model="trinity-large-thinking",
        context_length=256_000,
        config_threshold=0.50,
    )
    assert result == 0.75  # model override wins over everything


def test_resolve_compression_threshold_config_over_tier() -> None:
    """Config override beats adaptive tier when no model override."""
    result = resolve_compression_threshold(
        model="claude-sonnet-4",
        context_length=200_000,
        config_threshold=0.60,
    )
    assert result == 0.60  # config explicitly set


def test_resolve_compression_threshold_adaptive_tier_fallback() -> None:
    """When no config and no model override, adaptive tier decides."""
    result = resolve_compression_threshold(
        model="gpt-4o",
        context_length=128_000,
        config_threshold=None,
    )
    assert result == 0.85  # adaptive tier for 128K


def test_resolve_compression_threshold_no_config_small_model() -> None:
    """Small context model gets 0.60 from adaptive tier when no config."""
    result = resolve_compression_threshold(
        model="some-small-model",
        context_length=32_000,
        config_threshold=None,
    )
    assert result == 0.60