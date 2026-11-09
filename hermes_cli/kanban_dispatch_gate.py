"""Hold screen at the ONE choke point every dispatch path shares.

Why this file exists
--------------------
On 2026-11-03 a dependent card (`t_37ff7bcd`) spawned 84 seconds after its
parent completed, between two runs of the guarded ten minute council cron. The
producer was not a cron, not the gateway dispatcher (disabled in the root
config), not the idle ramp and not launchd: it was a manual
``hermes kanban --board council dispatch --max 3`` pass run from an architect
session. The screen that existed lived in ``council-tick.py``, a WRAPPER around
that same CLI, so every other caller spawned straight past it.

The four callers of :func:`hermes_cli.kanban_db_dispatch.dispatch_once` are:

* ``hermes kanban dispatch``              (``hermes_cli/kanban_ops.py``)
* the gateway's embedded dispatcher       (``gateway/kanban_watchers_dispatcher.py``)
* the dashboard nudge                     (``plugins/kanban/dashboard/plugin_api.py``)
* the standalone dispatch daemon          (``kanban_db_dispatch.run_*_daemon``)

``dispatch_once`` is therefore the only place a screen can cover all of them.
It is also the only place a plugin hook cannot reach: every kanban hook in
``hermes_cli/plugins.py`` is documented observer-only ("returns ignored"), so
no supported hook can veto a spawn.

Contract
--------
* **No policy file -> inactive.** Automatic respawning is unchanged. This keeps
  the default behaviour byte-identical to before the gate existed.
* **Policy present -> ready cards that read as a production change, deploy,
  credential action or external send are refused a spawn** on the boards the
  policy names. They stay ``ready`` and are re-screened each tick; this module
  never mutates a card. Blocking one with a written reason remains
  ``council-tick.py``'s job, because that is a mutation and needs a reason a
  human can read.
* **``hold_all: true``** (or the legacy ``~/.hermes/kanban/HOLD`` file the
  dispatch-window governor already honours) refuses every spawn, on every board.
* **Malformed policy -> FAIL CLOSED.** A hold that silently disappears is worse
  than a noisy one. An unreadable file is a misconfiguration, not an absence.

Activation is per PROCESS, so it is honest to state the matrix:

============================  =====================================
caller                        gate live
============================  =====================================
``hermes kanban dispatch``    immediately (fresh interpreter)
cron wrappers invoking it     immediately (fresh interpreter)
gateway dispatcher            after the next gateway restart
dashboard ``POST /dispatch``  after the next desktop restart
standalone daemon             on start (never started by this work)
============================  =====================================

Everything here is stdlib only so the module can be imported by a long-lived
gateway tick, a CLI process and a test harness without side effects.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("hermes_cli.kanban_dispatch_gate")

POLICY_VERSION = 1

#: Machine-editable policy. JSON is accepted too (same keys) so a caller that
#: has no YAML dependency can still install one.
DEFAULT_POLICY_PATH = "~/.hermes/kanban/DISPATCH-SCREEN.yaml"

#: Legacy total hold used by ``kanban-dispatch-governor.sh``. Presence means
#: "spawn nothing", with no parsing and no schema. Kept so an operator's
#: existing escape hatch keeps working.
LEGACY_HOLD_FILE = "~/.hermes/kanban/HOLD"

# --- The screen itself -------------------------------------------------------
# These lists are copied verbatim from council-tick.py, which has been the
# production screen since 2026-11-03. tests/test_kanban_dispatch_gate.py asserts
# the two stay equal, so a rule added to one that is missing from the other is a
# test failure rather than a silent coverage hole.

TITLE_HOLD = [
    (r"\bprod\b", "production change"),
    (r"production database", "production change"),
    (r"live database", "production change"),
    (r"deploy.{0,20}production|production.{0,20}deploy", "deploy"),
    (r"\bredeploy\b", "deploy"),
    (r"release gate", "deploy or release gate"),
    (r"\bflip\b", "configuration flip"),
    (r"\brollout\b", "rollout"),
    (r"\brotat(e|ion)\b", "credential rotation"),
    (r"\brevoke\b", "credential revocation"),
    (r"\bretention\b", "retention policy"),
    (r"\bcredential", "credential handling"),
    (r"\bpurge\b", "destructive deletion"),
    (r"\bstripe\b", "payment action"),
    (r"\bsms\b", "external send"),
    (r"\bemail\b", "external send"),
]

BODY_HOLD = [
    (r"gsutil rm\b", "destructive bucket deletion"),
    (r"api_tokens/\S+", "credential rotation"),
    (r"kubectl (apply|delete|patch|edit)", "production change"),
    (r"gcloud run (deploy|update|delete)", "deploy"),
    (r"gcloud sql (databases )?(execute-query|users (insert|delete))", "production database change"),
    (r"hermes kanban (complete|block).*prod", "production action"),
    (r"send (an |the )?(email|slack message|telegram|sms)", "external send"),
    (r"post to (slack|twitter|x|instagram)", "external send"),
    (r"delete (the )?(bucket|secret|project)", "destructive deletion"),
]

LOCAL_MARKERS = (
    r"\blocal\b",
    r"\bhomebrew\b",
    r"throwaway",
    r"empty database",
    r"fresh database",
    r"disposable",
    r"ephemeral",
    r"\bsandbox\b",
    r"\bfixture",
    r"\bunit test\b",
    r"docker (compose|container)",
)

SELF_SCOPED = (
    r"no deploy",
    r"no production change",
    r"no external send",
    r"commit locally only",
    r"local branch only",
)

PROD_TARGET = (
    r"\bprod\b",
    r"production",
    r"pg18-test",
    r"vflow-496309",
    r"cloud sql",
    r"cloudsql",
    r"secret manager",
    r"\blive\b",
)

#: A card the architect explicitly authorised is out of the screen's reach.
AUTHORISED_MARKER = "SCOPE-AUTHORIZED-BY-ARCHITECT"

#: Assignees whose cards are a human lane, never an agent dispatch.
TERMINAL_ASSIGNEES = {"san", "ops"}


@dataclass(frozen=True)
class Policy:
    """Resolved screen policy. ``source`` is what the decision is attributed to."""

    boards: frozenset[str] = frozenset()
    hold_all: bool = False
    hold_reason: str = ""
    source: str = "none"

    def covers(self, board: Optional[str]) -> bool:
        if "*" in self.boards:
            return True
        return (board or "") in self.boards


@dataclass
class Decision:
    """One tick's gate verdict."""

    policy: Policy
    hold_reason: str = ""
    hits: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.hold_reason


@dataclass
class _CacheEntry:
    stamp: tuple[int, int]
    policy: Policy


_cache: dict[str, _CacheEntry] = {}


def _kanban_home() -> Path:
    try:
        from hermes_cli import kanban_db as kb

        return kb.kanban_home()
    except Exception:
        return Path(os.path.expanduser("~/.hermes/kanban"))


def _policy_path(path: Optional[str] = None) -> Path:
    explicit = path or os.environ.get("HERMES_DISPATCH_SCREEN")
    if explicit:
        return Path(os.path.expanduser(explicit))
    return _kanban_home() / "DISPATCH-SCREEN.yaml"


def _parse_policy(text: str) -> dict:
    """Parse YAML when available, otherwise JSON. Raises on malformed content."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        loaded = json.loads(text)
    else:
        import yaml  # imported lazily so a JSON-only install never needs it

        loaded = yaml.safe_load(text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("policy root must be a mapping")
    return loaded


def load_policy(path: Optional[str] = None) -> Policy:
    """Resolve the policy for this tick.

    Absence is a decision (inactive), so it is cached like any other. A parse or
    schema error is NOT absence: it returns a ``hold_all`` policy whose reason
    names the fault, which is the fail-closed branch.
    """
    p = _policy_path(path)
    try:
        st = p.stat()
    except FileNotFoundError:
        return Policy(source="absent")
    except OSError as exc:
        return Policy(hold_all=True, hold_reason=f"dispatch screen unreadable: {exc}", source=str(p))

    stamp = (st.st_mtime_ns, st.st_size)
    cached = _cache.get(str(p))
    if cached is not None and cached.stamp == stamp:
        return cached.policy

    try:
        raw = _parse_policy(p.read_text(encoding="utf-8"))
        version = raw.get("version", POLICY_VERSION)
        if version != POLICY_VERSION:
            raise ValueError(f"unsupported policy version {version!r}")
        boards = raw.get("boards") or []
        if not isinstance(boards, (list, tuple)):
            raise ValueError("boards must be a list")
        hold_all = bool(raw.get("hold_all", False))
        hold_reason = str(raw.get("hold_reason") or "").strip()
        policy = Policy(
            boards=frozenset(str(b) for b in boards),
            hold_all=hold_all,
            hold_reason=hold_reason or ("hold_all is set in " + str(p) if hold_all else ""),
            source=str(p),
        )
    except Exception as exc:
        policy = Policy(
            hold_all=True,
            hold_reason=f"dispatch screen policy invalid ({type(exc).__name__}: {exc}) at {p}",
            source=str(p),
        )
        logger.warning("%s", policy.hold_reason)

    _cache[str(p)] = _CacheEntry(stamp=stamp, policy=policy)
    return policy


def legacy_hold() -> Optional[str]:
    """The pre-existing total hold file, if present. Content is the reason."""
    p = _kanban_home() / "HOLD"
    try:
        if not p.exists():
            return None
    except OSError:
        return f"dispatch hold state unreadable at {p}"
    try:
        text = p.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or f"HOLD file present at {p}"


def screen_task(title: str, body: str, assignee: str = "") -> Optional[str]:
    """Return the hold reason for one card, or ``None`` when it is clear.

    Pure and side-effect free so the same predicate can be unit tested against
    fixtures without a board.
    """
    if (assignee or "").strip().lower() in TERMINAL_ASSIGNEES:
        return None
    title = title or ""
    body = body or ""
    if AUTHORISED_MARKER in body:
        return None

    for pattern, label in TITLE_HOLD:
        if re.search(pattern, title, re.IGNORECASE):
            return label

    local = any(re.search(p, body, re.IGNORECASE) for p in LOCAL_MARKERS)
    self_scoped = any(re.search(p, body, re.IGNORECASE) for p in SELF_SCOPED)
    prod_target = any(re.search(p, body, re.IGNORECASE) for p in PROD_TARGET)

    for pattern, label in BODY_HOLD:
        if not re.search(pattern, body, re.IGNORECASE):
            continue
        if local and not prod_target:
            continue
        if self_scoped and label not in ("destructive bucket deletion", "credential rotation"):
            continue
        return label

    if prod_target and not local:
        if re.search(r"prisma migrate (deploy|resolve)|psql .*(alter|create|insert)", body, re.IGNORECASE):
            return "production database change"
    return None


def screen_rows(rows: Iterable[Any]) -> list[tuple[str, str, str]]:
    """Screen mapping-ish rows carrying ``id``, ``title``, ``body``, ``assignee``.

    ``evaluate`` hands over plain dicts so this stays independent of the caller's
    ``row_factory`` (a raw connection returns tuples, and ``sqlite3.Row`` is not a
    ``dict``).
    """
    hits: list[tuple[str, str, str]] = []
    for row in rows:
        reason = screen_task(
            str(row.get("title") or ""),
            str(row.get("body") or ""),
            str(row.get("assignee") or ""),
        )
        if reason:
            hits.append((str(row.get("id")), str(row.get("title") or ""), reason))
    return hits


def evaluate(
    conn,
    board: Optional[str],
    *,
    policy_path: Optional[str] = None,
    task_ids: Optional[Iterable[str]] = None,
    dry_run: bool = False,
) -> Decision:
    """One tick's verdict: whole-board hold first, then the per-card screen.

    ``task_ids`` limits the screen to the rows the tick is about to consider, so
    a busy board costs one primary-key lookup per spawnable card rather than a
    full table scan. ``dry_run`` changes nothing here; the caller decides what to
    do with the verdict, and reporting it on a dry run is the point.
    """
    policy = load_policy(policy_path)
    decision = Decision(policy=policy)

    hold = legacy_hold()
    if hold is not None:
        decision.hold_reason = hold
        return decision
    if policy.hold_all:
        decision.hold_reason = policy.hold_reason or "hold_all"
        return decision
    if not policy.covers(board) or not task_ids:
        return decision

    ids = [t for t in task_ids]
    placeholders = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(
            f"SELECT id, title, coalesce(body,'') AS body, coalesce(assignee,'') AS assignee "
            f"FROM tasks WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    except Exception as exc:
        # The screen could not read the cards. Spawning them would be the
        # pre-gate behaviour, so stay there and make the reason visible.
        logger.warning("dispatch screen could not read cards for board %s: %s", board, exc)
        return decision

    # Normalise to dicts: the caller's connection may or may not set a
    # row_factory, and screen_rows must not care.
    decision.hits = screen_rows(
        {
            "id": r[0],
            "title": r[1],
            "body": r[2],
            "assignee": r[3],
        }
        for r in rows
    )
    return decision


def describe(decision: Decision) -> str:
    """One-line reason suitable for a log line or a dispatch result."""
    if decision.hold_reason:
        return f"hold: {decision.hold_reason}"
    if decision.hits:
        return f"screened {len(decision.hits)} card(s): " + ", ".join(h[0] for h in decision.hits[:5])
    return "clear"


def reset_cache() -> None:
    """Test hook: forget cached policies so a rewritten file is re-read."""
    _cache.clear()


def is_active(policy: Optional[Policy] = None) -> bool:
    """True when the gate would do anything at all."""
    policy = policy or load_policy()
    return policy.hold_all or bool(policy.boards) or policy.source not in ("absent", "none")
