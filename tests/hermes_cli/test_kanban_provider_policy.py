"""Dispatch guards: banned provider pins and duplicate landing cards.

The cases replay the two live failures of 2026-09-27: six cards pinned to the
barred DeepSeek provider, and three cards racing to land jack-exec cb71128.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd
from hermes_cli import kanban_provider_policy as kpp

POLICY = "banned_providers: [deepseek, opencode-go, opencode-zen]\n"


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "scripts").mkdir(parents=True)
    (home / "scripts" / "routing-policy.yaml").write_text(POLICY, encoding="utf-8")
    for repo in ("jack-exec", "jack-slack"):
        (tmp_path / "Projects" / "Project26" / "Agents" / repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_ROUTING_POLICY", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# --- banned provider pins ----------------------------------------------------

@pytest.mark.parametrize("model,provider,expected", [
    ("deepseek-flash", "deepseek", "deepseek"),
    ("deepseek-flash", None, "deepseek"),
    ("deepseek/deepseek-chat", "openrouter", None),
    ("deepseek/deepseek-chat", None, None),
    ("z-ai/glm-5.3-flash", "openrouter", None),
    ("kimi-k3", "opencode-go", "opencode-go"),
    ("gemini-3.8-flash", None, None),
])
def test_banned_pin_resolution(model, provider, expected):
    assert kpp.banned_pin(model, provider, banned={"deepseek", "opencode-go"}) == expected


def test_policy_file_is_the_source_of_truth(kanban_home):
    (kanban_home / "scripts" / "routing-policy.yaml").write_text("banned_providers: [acme]\n")
    assert kpp.banned_providers() == frozenset({"acme"})
    assert kpp.banned_pin("deepseek-flash", "deepseek") is None


def test_unreadable_policy_fails_closed(kanban_home):
    (kanban_home / "scripts" / "routing-policy.yaml").write_text("tiers: {}\n")
    assert "deepseek" in kpp.banned_providers()
    (kanban_home / "scripts" / "routing-policy.yaml").unlink()
    assert kpp.banned_providers() == kpp.FALLBACK_BANNED_PROVIDERS


def test_create_refuses_banned_pin(kanban_home):
    with kbc.connect() as conn:
        with pytest.raises(ValueError, match="banned provider"):
            kb.create_task(conn, title="x", assignee="backend",
                           model_override="deepseek-flash", provider_override="deepseek")
        tid = kb.create_task(conn, title="y", assignee="backend",
                             model_override="z-ai/glm-5.3-flash", provider_override="openrouter")
        assert kb.get_task(conn, tid).provider_override == "openrouter"


def test_set_model_refuses_banned_pin_but_clearing_works(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="z", assignee="backend")
        with pytest.raises(ValueError, match="banned provider"):
            kb.set_model_override(conn, tid, "deepseek-flash", provider="deepseek")
        assert kb.set_model_override(conn, tid, None) is True
        assert kb.get_task(conn, tid).model_override is None


def test_dispatch_strips_a_legacy_banned_pin(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="legacy", assignee="backend")
        # A row written before the create-time refusal existed.
        conn.execute("UPDATE tasks SET model_override='deepseek-flash', provider_override='deepseek' "
                     "WHERE id=?", (tid,))
        conn.commit()
        argv = kbd._worker_argv(kb.get_task(conn, tid), "backend", str(kanban_home))
    tail = argv[argv.index("hermes_cli.main") + 1:]
    assert "deepseek" not in " ".join(tail)
    assert "-m" not in tail and "--provider" not in tail


def test_dispatch_keeps_an_approved_pin(kanban_home):
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="ok", assignee="backend",
                             model_override="z-ai/glm-5.3-flash", provider_override="openrouter")
        argv = kbd._worker_argv(kb.get_task(conn, tid), "backend", str(kanban_home))
    tail = argv[argv.index("hermes_cli.main") + 1:]
    assert tail[tail.index("-m") + 1] == "z-ai/glm-5.3-flash"
    assert tail[tail.index("--provider") + 1] == "openrouter"


# --- duplicate landing cards -------------------------------------------------

# The three real cards of 2026-09-27 15:30 EDT (titles verbatim, bodies trimmed).
RACE = [
    ("jack-exec: land migration 0027_wallet_credit_cache (cb71128) on main so prod is not ahead "
     "of deployable code", "Serving image cb71128 is ahead of main."),
    ("Repair jack-exec landing inversion: land 0027_wallet_credit_cache so main is deployable again "
     "(blocks OIDC Phase-2)",
     "Source the repair from the commit `cb71128` (git show cb71128:migrations/...). "
     "The serving digest sha256:0708149adfe4 is unrelated."),
    ("Land wt/t_ffad1339 @ cb71128 (migration 0027_wallet_credit_cache) on jack-exec main - main is "
     "one migration behind", None),
]


def test_the_three_racing_cards_derive_one_key():
    keys = {kpp.derive_land_key(t, b, repos={"jack-exec", "jack-slack"}) for t, b in RACE}
    assert keys == {"land:jack-exec:cb71128"}


@pytest.mark.parametrize("title", [
    "validator: cold-diff review jack-exec wt/t_67f838b0 @ 4488aca",
    "jack-agi: roll the hero polish commit (616649d) to production",
    "Land the classifier fix on jack-slack main",
    "Land wt/t_ffad1339 on jack-exec main",
])
def test_non_land_or_shaless_titles_derive_nothing(title):
    assert kpp.derive_land_key(title, None, repos={"jack-exec", "jack-slack", "jack-agi"}) is None


def test_second_land_card_returns_the_first(kanban_home):
    with kbc.connect() as conn:
        ids = [kb.create_task(conn, title=t, body=b, assignee="backend") for t, b in RACE]
        assert len(set(ids)) == 1
        assert kb.get_task(conn, ids[0]).idempotency_key == "land:jack-exec:cb71128"


def test_archived_land_card_does_not_block_a_retry(kanban_home):
    with kbc.connect() as conn:
        first = kb.create_task(conn, title=RACE[0][0], assignee="backend")
        conn.execute("UPDATE tasks SET status='archived' WHERE id=?", (first,))
        conn.commit()
        second = kb.create_task(conn, title=RACE[2][0], assignee="backend")
        assert second != first
