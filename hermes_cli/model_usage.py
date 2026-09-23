"""Lifetime per-route model usage for the model picker.

``session_model_usage`` stores one row per (session, model, billing route, task). The picker wants
one *lifetime* view per **route**: the same model id served by two providers, or by one provider on
two base URLs, is two different things to choose between, so those must never collapse into one
number. A route is therefore keyed on ``(billing_provider, billing_base_url, model)`` end to end.

Read-only and fail-open by contract: a missing store or table, a locked/unreadable DB, or a route
that cannot be mapped onto exactly one catalog row leaves the caller's catalog exactly as it was,
with no usage metadata at all.

Contract (``model.options`` → ``ModelOptionProvider.usage``)::

    row["usage"] = {
        "<model id>": {
            "api_call_count": 12,          # summed over this row's routes
            "last_used_at": 1790205585.0,  # max last_seen, unix seconds; None when unknown
            "routes": [                    # one entry per distinct billing base URL
                {"base_url": "https://api.example.test/v1",
                 "api_call_count": 12, "last_used_at": 1790205585.0},
            ],
        },
    }

Models a row does not list are still reported: a raw custom endpoint's catalog is probed, so the
history of a model the probe did not return is exactly what a picker wants to surface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

# ``task = ''`` is the main agent loop; every other value is an auxiliary call (vision, compression,
# title_generation, background_review, ...) billed to a different route, with its own provider.
# ``model != ''`` guards a healed legacy row that carries no usable id from minting a phantom entry.
_ROUTE_USAGE_SQL = """
    SELECT model,
           billing_provider,
           billing_base_url,
           COALESCE(SUM(api_call_count), 0) AS api_call_count,
           MAX(last_seen) AS last_used_at
      FROM session_model_usage
     WHERE task = '' AND model != ''
     GROUP BY model, billing_provider, billing_base_url
"""


def load_route_usage(db_path: Optional[Path] = None) -> list[dict]:
    """Aggregate lifetime main-loop usage rows for the ACTIVE profile's state store.

    ``db_path`` defaults to the profile's own ``state.db`` (``hermes_state._default_db_path()``,
    which resolves ``get_hermes_home()`` at call time) — so a caller already inside a profile scope
    reads THAT profile's usage and never the launch profile's. Never raises: any failure (absent
    file, absent table, locked or unreadable store) yields ``[]``.
    """
    try:
        from hermes_state import SessionDB, _default_db_path

        path = Path(db_path) if db_path is not None else Path(_default_db_path())
        if not path.exists():
            return []
        db = SessionDB(db_path=path, read_only=True)
    except Exception:
        return []

    try:
        rows = db._read_all(_ROUTE_USAGE_SQL)
    except Exception:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass

    usage: list[dict] = []
    for row in rows:
        model = str(row["model"] or "").strip()
        if not model:
            continue
        last_seen = row["last_used_at"]
        usage.append({
            "model": model,
            "provider": str(row["billing_provider"] or "").strip(),
            "base_url": str(row["billing_base_url"] or "").strip(),
            "api_call_count": int(row["api_call_count"] or 0),
            "last_used_at": float(last_seen) if last_seen is not None else None,
        })
    return usage


def attach_route_usage(rows: list[dict], usage: Iterable[dict]) -> None:
    """Attach ``row["usage"]`` to every catalog row a stored route resolves to.

    Each route is placed on exactly one row (see :func:`_match_row_index`); rows with no routes stay
    untouched, so an install with no history keeps the payload it had. Never raises.
    """
    try:
        routes = [r for r in (usage or []) if r]
        if not routes or not rows:
            return

        index = _identity_index(rows)
        matched: dict[int, list[dict]] = {}
        for route in routes:
            pos = _match_row_index(index, rows, route)
            if pos is not None:
                matched.setdefault(pos, []).append(route)

        for pos, row in enumerate(rows):
            entry = _row_usage(matched.get(pos) or [])
            if entry:
                row["usage"] = entry
    except Exception:
        return  # fail open: usage metadata is optional, the catalog is not


# ─── Route → row mapping ────────────────────────────────────────────────


def _normalized_url(url: Any) -> str:
    return str(url or "").strip().rstrip("/").lower()


def _row_identities(row: dict) -> set[str]:
    """Every spelling a stored ``billing_provider`` can use for this row: its slug plus the aliases
    the inventory publishes for user-defined rows (``custom:<key>``, the bare key, the display name).
    """
    values = [row.get("slug"), *(row.get("aliases") or [])]
    return {str(value).strip().lower() for value in values if str(value or "").strip()}


def _identity_index(rows: list[dict]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for pos, row in enumerate(rows):
        for key in _row_identities(row):
            index.setdefault(key, []).append(pos)
    return index


def _provider_keys(provider: Any) -> list[str]:
    """Lookup spellings for a stored provider id, without inventing any.

    A ``custom:`` id is NOT split into its bare suffix here: the suffix is frequently a real
    provider slug (``custom:openai`` → ``openai`` would steal that row's identity). User-defined
    rows publish the bare form themselves through ``aliases``, so nothing is lost.
    """
    raw = str(provider or "").strip().lower()
    if not raw:
        return []
    keys = [raw]
    try:
        from hermes_cli.providers import normalize_provider

        canonical = str(normalize_provider(raw) or "").strip().lower()
    except Exception:
        canonical = ""
    if canonical and canonical not in keys:
        keys.append(canonical)
    return keys


def _match_row_index(index: dict[str, list[int]], rows: list[dict], route: dict) -> Optional[int]:
    """The single catalog row that owns *route*, or ``None`` when nothing (or more than one thing)
    plausibly does. Precedence, each rung requiring a UNIQUE answer:

    1. an exact ``slug`` match — the row the stored provider id names outright;
    2. a unique ``api_url`` equal to the route's billing base URL (both real URLs);
    3. a unique candidate overall (the provider id resolved to one row).

    Anything else is ambiguous: two opaque aliases could both be "the" endpoint, and attributing a
    real spend history to the wrong provider is worse than showing none.
    """
    candidates: list[int] = []
    for key in _provider_keys(route.get("provider")):
        for pos in index.get(key, []):
            if pos not in candidates:
                candidates.append(pos)
    if not candidates:
        return None

    stored = str(route.get("provider") or "").strip().lower()
    slug_matches = [pos for pos in candidates if str(rows[pos].get("slug") or "").strip().lower() == stored]
    if len(slug_matches) == 1:
        return slug_matches[0]

    route_url = _normalized_url(route.get("base_url"))
    if route_url:
        url_matches = [pos for pos in candidates
                       if _normalized_url(rows[pos].get("api_url")) == route_url]
        if len(url_matches) == 1:
            return url_matches[0]

    return candidates[0] if len(candidates) == 1 else None


# ─── Route list → row["usage"] ──────────────────────────────────────────


def _row_usage(routes: list[dict]) -> dict[str, dict]:
    """Group matched routes by model, then by billing base URL inside each model.

    Base URLs are grouped by their normalized form: the agent loop has recorded the same endpoint
    both with and without a trailing slash (``.../v1`` and ``.../v1/``), and those spellings are one
    route with one lifetime count, not two. The reported ``base_url`` is the spelling the newest row
    used, so the value the picker shows is one the store actually holds.
    """
    per_model: dict[str, dict[str, dict]] = {}
    for route in routes:
        model = str(route.get("model") or "").strip()
        if not model:
            continue
        raw_url = str(route.get("base_url") or "")
        by_url = per_model.setdefault(model, {})
        entry = by_url.setdefault(
            _normalized_url(raw_url), {"base_url": raw_url, "api_call_count": 0, "last_used_at": None})
        entry["api_call_count"] += int(route.get("api_call_count") or 0)
        last_used = route.get("last_used_at")
        if last_used is not None and (entry["last_used_at"] is None or last_used > entry["last_used_at"]):
            entry["last_used_at"] = last_used
            entry["base_url"] = raw_url

    usage: dict[str, dict] = {}
    for model, by_url in per_model.items():
        urls = sorted(by_url.values(), key=lambda e: (e["last_used_at"] is None, -(e["last_used_at"] or 0)))
        usage[model] = {
            "api_call_count": sum(e["api_call_count"] for e in urls),
            "last_used_at": max((e["last_used_at"] for e in urls if e["last_used_at"] is not None),
                                default=None),
            "routes": urls,
        }
    return usage
