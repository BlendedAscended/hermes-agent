"""Lifetime per-route usage (``session_model_usage``) on the shared model-options payload.

Route identity is ``(provider, base URL, model)``: the same model id on two providers, or on one
provider serving two base URLs, must arrive as separate numbers and never merge on model id alone.
Auxiliary-call rows stay out, the aggregation is a lifetime sum/max, and a store/route that cannot
be read or mapped leaves the catalog exactly as it was built.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import hermes_cli.inventory as inv
from hermes_cli.model_usage import attach_route_usage, load_route_usage
from hermes_state import SessionDB

A_URL = "https://a.test/v1"
B_URL = "https://b.test/v1"


def _seed_state_db(home: Path, rows: list[tuple], *, session_id: str = "s1") -> Path:
    """Write ``session_model_usage`` rows the way the agent loop does: one row per
    (session, model, provider, base URL, task), with the PK dictating one row per route."""
    home.mkdir(parents=True, exist_ok=True)
    db = SessionDB(home / "state.db")
    try:
        db.create_session(session_id=session_id, source="tui", model="alpha")
        with db._lock:
            db._conn.executemany(
                "INSERT INTO session_model_usage (session_id, model, billing_provider,"
                " billing_base_url, task, api_call_count, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(session_id, *row) for row in rows],
            )
            db._conn.commit()
    finally:
        db.close()
    return home / "state.db"


def _row(slug: str, **extra) -> dict:
    row = {"slug": slug, "name": slug.title(), "models": [], "total_models": 0,
           "is_current": False, "is_user_defined": slug.startswith("custom:"), "source": "test"}
    row.update(extra)
    return row


# ─── Aggregation ────────────────────────────────────────────────────────


def test_lifetime_usage_sums_main_loop_calls_and_excludes_auxiliary_tasks(tmp_path):
    """Two sessions on one route sum; a ``task`` row on the same route is a different spender."""
    db_path = _seed_state_db(tmp_path, [
        ("alpha", "deepseek", A_URL, "", 5, 100.0),
        ("alpha", "deepseek", A_URL, "vision", 900, 250.0),
    ])
    _seed_state_db(tmp_path, [("alpha", "deepseek", A_URL, "", 7, 200.0)], session_id="s2")

    usage = load_route_usage(db_path)
    assert [(u["model"], u["api_call_count"]) for u in usage] == [("alpha", 12)], (
        "lifetime usage must fold every session's main-loop calls into one route and drop the "
        f"auxiliary call: {usage}"
    )

    rows = [_row("deepseek")]
    attach_route_usage(rows, usage)
    assert rows[0]["usage"]["alpha"]["api_call_count"] == 12
    assert rows[0]["usage"]["alpha"]["last_used_at"] == 200.0


def test_trailing_slash_spellings_are_one_route_not_two(tmp_path):
    """The loop records the same endpoint both with and without a trailing slash."""
    db_path = _seed_state_db(tmp_path, [
        ("alpha", "deepseek", A_URL, "", 4, 100.0),
        ("alpha", "deepseek", f"{A_URL}/", "", 6, 300.0),
    ])

    rows = [_row("deepseek")]
    attach_route_usage(rows, load_route_usage(db_path))

    alpha = rows[0]["usage"]["alpha"]
    assert alpha["api_call_count"] == 10
    assert len(alpha["routes"]) == 1, f"one endpoint, one route entry: {alpha['routes']}"
    assert alpha["routes"][0]["base_url"] == f"{A_URL}/", "report the newest spelling"
    assert alpha["last_used_at"] == 300.0


def test_last_used_at_is_the_newest_route_use_regardless_of_row_order(tmp_path):
    """Recency is a MAX over the route, not "the row the loop touched last".

    One row per session is the real shape (the PK is session-scoped), so the newest *use* and the
    newest *row* are deliberately different sessions here."""
    _seed_state_db(tmp_path, [("alpha", "deepseek", A_URL, "", 1, 500.0)], session_id="newest-use")
    _seed_state_db(tmp_path, [("alpha", "deepseek", A_URL, "", 1, 100.0)], session_id="oldest-use")
    _seed_state_db(tmp_path, [("alpha", "deepseek", A_URL, "", 1, 900.0)], session_id="newest-row")

    rows = [_row("deepseek")]
    attach_route_usage(rows, load_route_usage(tmp_path / "state.db"))
    alpha = rows[0]["usage"]["alpha"]
    assert alpha["api_call_count"] == 3
    assert alpha["last_used_at"] == 900.0
    assert alpha["routes"][0]["last_used_at"] == 900.0


def test_same_model_id_on_two_routes_keeps_both_numbers(tmp_path):
    """One model id, two providers AND two base URLs: four distinct routes, no cross-talk."""
    db_path = _seed_state_db(tmp_path, [
        ("shared/model", "custom:proxy-a", A_URL, "", 3, 100.0),
        ("shared/model", "custom:proxy-a", B_URL, "", 11, 150.0),
        ("shared/model", "custom:proxy-b", B_URL, "", 7, 400.0),
    ])

    rows = [
        _row("custom:proxy-a", aliases=["proxy-a"], api_url=A_URL),
        _row("custom:proxy-b", aliases=["proxy-b"], api_url=B_URL),
    ]
    attach_route_usage(rows, load_route_usage(db_path))

    first, second = rows[0]["usage"]["shared/model"], rows[1]["usage"]["shared/model"]
    assert first["api_call_count"] == 14, "proxy-a owns only its own two base URLs"
    assert second["api_call_count"] == 7, "proxy-b must not absorb proxy-a's calls"
    assert {route["base_url"] for route in first["routes"]} == {A_URL, B_URL}
    assert [route["base_url"] for route in second["routes"]] == [B_URL]
    assert first["last_used_at"] == 150.0 and second["last_used_at"] == 400.0


def test_provider_string_matches_a_row_by_its_published_identity(tmp_path):
    """``billing_provider`` may hold any spelling the inventory publishes for the endpoint, so the
    match is run against the row's own identity set rather than a hardcoded alias list."""
    from hermes_cli.providers import custom_provider_aliases

    slug, name = "custom:proxy-a", "Proxy A"
    aliases = sorted(custom_provider_aliases(name, slug))

    for provider in (slug, "proxy-a", name):
        home = tmp_path / provider.replace(":", "_").replace(" ", "_")
        rows = [_row(slug, name=name, aliases=aliases, api_url=A_URL)]
        attach_route_usage(rows, load_route_usage(_seed_state_db(
            home, [("alpha", provider, A_URL, "", 2, 10.0)])))
        assert rows[0]["usage"]["alpha"]["api_call_count"] == 2, f"{provider} failed to resolve"


def test_ambiguous_or_unknown_route_is_omitted_not_misattributed():
    """Two rows share an alias and neither matches by slug or URL: show nothing, blame nobody."""
    rows = [
        _row("custom:proxy-a", aliases=["shared"], api_url=A_URL),
        _row("custom:proxy-b", aliases=["shared"], api_url=B_URL),
    ]
    attach_route_usage(rows, [
        {"model": "alpha", "provider": "shared", "base_url": "https://c.test/v1",
         "api_call_count": 5, "last_used_at": 1.0},
        {"model": "alpha", "provider": "nowhere", "base_url": A_URL,
         "api_call_count": 5, "last_used_at": 1.0},
    ])
    assert "usage" not in rows[0] and "usage" not in rows[1]


def test_base_url_separates_two_rows_that_share_a_provider_alias():
    """Opaque alias, two endpoints: the billing base URL is what decides the owner."""
    rows = [
        _row("custom:proxy-a", aliases=["shared"], api_url=A_URL),
        _row("custom:proxy-b", aliases=["shared"], api_url=B_URL),
    ]
    attach_route_usage(rows, [{"model": "alpha", "provider": "shared", "base_url": f"{B_URL}/",
                               "api_call_count": 9, "last_used_at": 1.0}])
    assert "usage" not in rows[0]
    assert rows[1]["usage"]["alpha"]["api_call_count"] == 9


# ─── Fail-open ──────────────────────────────────────────────────────────


def test_missing_store_and_empty_usage_leave_the_catalog_untouched(tmp_path):
    assert load_route_usage(tmp_path / "absent.db") == []

    rows = [_row("deepseek")]
    attach_route_usage(rows, [])
    attach_route_usage(rows, [])
    assert "usage" not in rows[0], "an install with no history keeps the payload it had"

    empty_db = _seed_state_db(tmp_path, [])
    attach_route_usage(rows, load_route_usage(empty_db))
    assert "usage" not in rows[0]


def test_unreadable_store_fails_open(tmp_path):
    """A non-SQLite file where state.db should be must not raise into the picker."""
    broken = tmp_path / "state.db"
    broken.write_bytes(b"not a database")
    assert load_route_usage(broken) == []


# ─── Owner profile ──────────────────────────────────────────────────────


@pytest.fixture
def live_default_db_path(monkeypatch):
    """Put ``_default_db_path()`` back on its ``get_hermes_home()`` branch.

    The hermetic conftest fixture re-pins ``hermes_state.DEFAULT_DB_PATH`` to ITS own temp home, and
    a pinned constant wins over the resolved home — which would mask exactly the resolution these
    tests are about. Restoring the import-time sentinel makes the call resolve the bound home.
    """
    import hermes_state

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH)


def test_usage_follows_the_active_profile_store(tmp_path, monkeypatch, live_default_db_path):
    """A→B→A: the same call reads whichever profile's ``state.db`` is bound, every time."""
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    _seed_state_db(home_a, [("alpha", "deepseek", A_URL, "", 3, 10.0)])
    _seed_state_db(home_b, [("beta", "openrouter", B_URL, "", 4, 20.0)])

    seen = []
    for home in (home_a, home_b, home_a):
        monkeypatch.setenv("HERMES_HOME", str(home))
        seen.append(sorted((u["model"], u["api_call_count"]) for u in load_route_usage()))

    assert seen == [[("alpha", 3)], [("beta", 4)], [("alpha", 3)]], (
        "usage must never leak across profiles or go stale after re-pointing HERMES_HOME"
    )


def test_a_profile_with_no_store_loses_nothing(tmp_path, monkeypatch, live_default_db_path):
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    _seed_state_db(home_a, [("alpha", "deepseek", A_URL, "", 3, 10.0)])
    home_b.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(home_a))
    assert load_route_usage()
    monkeypatch.setenv("HERMES_HOME", str(home_b))
    assert load_route_usage() == []
    monkeypatch.setenv("HERMES_HOME", str(home_a))
    assert load_route_usage()


# ─── Wiring: the shared payload ─────────────────────────────────────────


def _options_payload(monkeypatch, rows: list[dict]) -> dict:
    """``build_model_options_payload`` with the network/pricing edges stubbed out."""
    monkeypatch.setattr("hermes_cli.model_switch.list_authenticated_providers", lambda **_kw: list(rows))
    monkeypatch.setattr(inv, "_moa_provider_row", lambda *_a, **_kw: None)
    monkeypatch.setattr(inv, "_apply_capabilities", lambda _rows: None)
    monkeypatch.setattr(inv, "_apply_featured", lambda _rows: None)
    monkeypatch.setattr(inv, "_prewarm_pricing_async", lambda *_a, **_kw: None)
    monkeypatch.setattr(inv, "_apply_pricing", lambda *_a, **_kw: None)
    return inv.build_model_options_payload(
        inv.ConfigContext(current_provider="deepseek", current_model="alpha", current_base_url=A_URL,
                          user_providers={}, custom_providers=[]),
    )


def test_model_options_payload_carries_lifetime_usage_per_route(
    tmp_path, monkeypatch, live_default_db_path,
):
    """The end-to-end contract: store → ``load_route_usage`` → ``build_model_options_payload``."""
    _seed_state_db(tmp_path, [
        ("deepseek-flash", "deepseek", A_URL, "", 42, 1700000000.0),
        ("deepseek-flash", "openrouter", B_URL, "", 11, 1600000000.0),
    ])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    payload = _options_payload(monkeypatch, [_row("deepseek"), _row("openrouter")])
    providers = {row["slug"]: row for row in payload["providers"]}

    assert providers["deepseek"]["usage"] == {
        "deepseek-flash": {"api_call_count": 42, "last_used_at": 1700000000.0,
                           "routes": [{"base_url": A_URL, "api_call_count": 42,
                                       "last_used_at": 1700000000.0}]},
    }
    assert providers["openrouter"]["usage"]["deepseek-flash"]["api_call_count"] == 11


def test_stale_or_absent_store_leaves_the_payload_unchanged(
    tmp_path, monkeypatch, live_default_db_path,
):
    """No store at all: the picker payload is exactly what the builder produced."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty"))
    payload = _options_payload(monkeypatch, [_row("deepseek")])
    assert "usage" not in payload["providers"][0]
