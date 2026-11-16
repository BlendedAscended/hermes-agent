"""Explicit cron egress grants never participate in inbound profile routing."""
from pathlib import Path

from gateway.profile_routing import ProfileRoute, parse_profile_routes


class OutboundDeliveryRoute(ProfileRoute):
    """A channel grant does not implicitly grant every thread under that channel."""

    def matches(self, platform, *, thread_id=None, **kwargs):
        return thread_id == self.thread_id and super().matches(platform, thread_id=thread_id, **kwargs)


def primary_outbound_routes(layered: dict, current_home: Path) -> list:
    """Return exact grants only while a proven live primary serves this profile and transport."""
    from gateway.status import multiplexer_liveness_for_profile, profile_name_for_home, runtime_status_is_stale
    from hermes_cli.profiles import profile_matches_home

    cron = layered.get("cron")
    raw = cron.get("shared_delivery_targets") if isinstance(cron, dict) else None
    if not isinstance(raw, list):
        return []
    live = multiplexer_liveness_for_profile(current_home)
    if live is None:
        return []
    pid, runtime = live
    if (runtime.get("pid") != pid or runtime_status_is_stale(runtime)
            or profile_name_for_home(current_home) not in runtime.get("served_profiles", [])):
        return []
    platforms = runtime.get("platforms") or {}
    routes = []
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("enabled", True) is not True:
            continue
        if entry.keys() - {"name", "profile", "platform", "chat_id", "thread_id", "enabled"}:
            continue
        if not isinstance(entry.get("chat_id"), str) or not entry["chat_id"].strip():
            continue
        if "thread_id" in entry and (not isinstance(entry["thread_id"], str) or not entry["thread_id"].strip()):
            continue
        # Inbound discriminators must not silently broaden a copied configuration.
        fields = {k: entry[k] for k in ("name", "profile", "platform", "chat_id", "thread_id") if k in entry}
        for route in parse_profile_routes([fields]):
            transport = platforms.get(route.platform) or {}
            if profile_matches_home(route.profile) and transport.get("state") == "connected":
                routes.append(OutboundDeliveryRoute(**vars(route)))
    return routes
