"""Dispatch guards for Kanban cards: banned providers and duplicate landings.

Two failures on 2026-09-27 motivated this module, both measured live:

* Card-level ``model_override``/``provider_override`` bypassed fleet routing.
  Every profile config defaulted to OpenRouter and ADR-167 barred DeepSeek,
  yet six open cards still carried ``provider_override=deepseek`` and the
  dispatcher passed it straight to the worker argv.
* Three cards landing the same commit (jack-exec ``cb71128``) were created
  within 24 seconds and all claimed, racing to push the same repo's main.

The banned list lives in ONE place, the ASFR routing policy
(``~/.hermes/scripts/routing-policy.yaml``, key ``banned_providers``), so the
task classifier, card creation and the dispatcher agree. When that file is
missing or unreadable the last known list below applies, so a broken policy
file fails closed rather than silently re-admitting a barred provider.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

#: Used only when routing-policy.yaml cannot be read. Mirrors ADR-151 D5
#: (opencode-go, opencode-zen, 2026-09-26) and ADR-167 (deepseek, 2026-09-27).
FALLBACK_BANNED_PROVIDERS = frozenset({"deepseek", "opencode-go", "opencode-zen"})


class BannedProviderError(ValueError):
    """A card tried to pin a provider the routing policy bans."""


def _policy_path() -> Path:
    override = os.environ.get("HERMES_ROUTING_POLICY", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "scripts" / "routing-policy.yaml"


def banned_providers() -> frozenset[str]:
    """The routing policy's ``banned_providers``, lowercased.

    Read on every call (the file is small and cards are created rarely), so an
    edit to the policy takes effect without restarting the gateway.
    """
    path = _policy_path()
    try:
        import yaml  # PyYAML ships with hermes-agent

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        listed = data.get("banned_providers")
        if not isinstance(listed, list):
            raise ValueError("banned_providers is missing or not a list")
        return frozenset(str(p).strip().lower() for p in listed if str(p).strip())
    except Exception as exc:
        _log.warning(
            "kanban provider policy: cannot read banned_providers from %s (%s); "
            "failing closed with the last known list %s",
            path, exc, sorted(FALLBACK_BANNED_PROVIDERS),
        )
        return FALLBACK_BANNED_PROVIDERS


def banned_pin(model: Optional[str], provider: Optional[str],
               banned: Optional[Iterable[str]] = None) -> Optional[str]:
    """Return the banned provider a model/provider pin resolves to, else None.

    An explicit ``provider`` is authoritative. Without one, a model named after
    a banned provider (``deepseek-flash``, ``deepseek:chat``) is a config alias
    for that native provider, so it counts. A slash slug such as
    ``deepseek/deepseek-chat`` is an aggregator model id (OpenRouter), not the
    native provider, so it does not.
    """
    banned_set = frozenset(banned) if banned is not None else banned_providers()
    if provider:
        name = provider.strip().lower()
        return name if name in banned_set else None
    if model:
        name = model.strip().lower()
        for prov in banned_set:
            if name == prov or name.startswith(prov + "-") or name.startswith(prov + ":"):
                return prov
    return None


def refuse_banned_pin(model: Optional[str], provider: Optional[str]) -> None:
    """Raise BannedProviderError when the pin names a banned provider."""
    prov = banned_pin(model, provider)
    if prov:
        raise BannedProviderError(
            f"model override {model!r} (provider {provider!r}) resolves to banned "
            f"provider {prov!r}; the routing policy bars it. Omit the override so the "
            f"worker uses its profile default, or pick an approved provider."
        )


# --- Duplicate landing cards ------------------------------------------------

_LAND_WORD = re.compile(r"\b(land|landing|lands|merge)\b", re.IGNORECASE)
_SHA = r"[0-9a-f]{7,40}"
# A bare sha must stand alone: card ids (t_ffad1339) and branch names
# (wt/t_ffad1339) are hex too, so a run glued to ``_``, ``/`` or ``-`` is not one.
_TITLE_SHA = re.compile(rf"(?<![0-9a-zA-Z_/-]){_SHA}(?![0-9a-zA-Z_])")
# In a body, only a sha introduced as the landing subject counts, so digests,
# run ids and quoted base commits elsewhere in the text do not.
_BODY_SHA = re.compile(rf"(?:@|\bcommit\b|\bmerge\b|\bland\b)[\s`*]{{0,4}}({_SHA})(?![0-9a-zA-Z])",
                       re.IGNORECASE)
_REPO = re.compile(r"\b([a-z][a-z0-9]*(?:[-.][a-z0-9]+)+)\b")


def _known_repos() -> frozenset[str]:
    roots = [Path.home() / "Projects" / "Project26" / "Agents", Path.home() / "Projects" / "Project26"]
    names: set[str] = set()
    for root in roots:
        try:
            names.update(p.name.lower() for p in root.iterdir() if (p / ".git").exists())
        except OSError:
            continue
    return frozenset(names)


def derive_land_key(title: str, body: Optional[str] = None,
                    repos: Optional[Iterable[str]] = None) -> Optional[str]:
    """``land:<repo>:<sha7>`` for a card that lands a commit, else None.

    Only a card whose title says it lands or merges qualifies. The sha comes
    from the title, falling back to the first sha the body introduces with
    ``@``/``commit``/``merge``/``land``. The repo is the first known repo name
    in the title (checkouts under ~/Projects/Project26/Agents), falling back to
    the body. Both must be found, otherwise no key is derived and creation
    proceeds as before.
    """
    if not title or not _LAND_WORD.search(title):
        return None
    sha = None
    mi = _BODY_SHA.search(title)
    m = mi or _TITLE_SHA.search(title.lower())
    if m:
        sha = (mi.group(1) if mi else m.group(0)).lower()
    elif body:
        mb = _BODY_SHA.search(body)
        if mb:
            sha = mb.group(1).lower()
    if not sha:
        return None
    known = frozenset(r.lower() for r in repos) if repos is not None else _known_repos()
    repo = None
    for text in (title, body or ""):
        for cand in _REPO.findall(text.lower()):
            if cand in known:
                repo = cand
                break
        if repo:
            break
    if not repo:
        return None
    return f"land:{repo}:{sha[:7]}"
