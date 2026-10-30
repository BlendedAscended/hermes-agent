from __future__ import annotations

import pytest

from hermes_cli.kanban_runtime_policy import RuntimePolicyError, default_max_runtime_seconds


def test_default_null_cap_is_a_per_attempt_5400_second_fallback():
    assert default_max_runtime_seconds(dict) == 5400
    assert default_max_runtime_seconds(lambda: {"kanban": {}}) == 5400


@pytest.mark.parametrize("value", [0, 86400])
def test_explicit_global_policy_values_are_preserved(value):
    assert default_max_runtime_seconds(lambda: {"kanban": {"default_max_runtime_seconds": value}}) == value


@pytest.mark.parametrize("value", [True, -1, 1.5, "5400"])
def test_invalid_global_policy_fails_loudly(value):
    with pytest.raises(RuntimePolicyError):
        default_max_runtime_seconds(lambda: {"kanban": {"default_max_runtime_seconds": value}})


def test_unreadable_runtime_policy_refuses_the_tick():
    def fail():
        raise OSError("fixture config read failure")

    with pytest.raises(RuntimePolicyError, match="dispatch tick refused"):
        default_max_runtime_seconds(fail)
