"""A satellite cron may share an exact output target without taking inbound ownership."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import hermes_yaml as yaml
import pytest

from cron.scheduler_delivery import _resolve_target_transport
from cron.scheduler_preflight import (
    SharedRouteAdapters,
    _preflight_check_delivery,
    _primary_profile_routes_for_current_home,
)
from gateway.config import Platform
from gateway.profile_routing import match_profile_route
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def shared_home(tmp_path, monkeypatch):
    root = tmp_path / "root"
    home = root / "profiles" / "delivery"
    home.mkdir(parents=True)
    monkeypatch.setattr("hermes_constants.get_default_hermes_root", lambda: root)
    record = {"pid": 123, "served_profiles": ["default", "delivery"],
              "updated_at": datetime.now(UTC).isoformat(),
              "platforms": {"slack": {"state": "connected"}}}
    monkeypatch.setattr("gateway.status.multiplexer_liveness_for_profile", lambda _: (123, record))
    config = {"cron": {"shared_delivery_targets": [
        {"name": "brief-output", "profile": "delivery", "platform": "slack", "chat_id": "C12345678"}]}}
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    satellite = MagicMock()
    satellite.get_connected_platforms.return_value = []
    satellite.platforms = {}
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: satellite)
    token = set_hermes_home_override(str(home))
    try:
        yield root, record, config, satellite
    finally:
        reset_hermes_home_override(token)


def test_output_grant_passes_native_preflight_and_transport_without_inbound_route(shared_home):
    root, _, config, satellite = shared_home
    from gateway.config_loader import read_yaml_layers
    from gateway.profile_routing import parse_profile_routes

    # The gateway's own route reader remains empty; this grant cannot redirect inbound messages.
    loaded = read_yaml_layers(root)
    assert match_profile_route(parse_profile_routes(loaded.get("profile_routes")), "slack", chat_id="C12345678") is None
    assert _preflight_check_delivery({"deliver": "slack:C12345678"}) is None
    adapter = object()
    shared = SharedRouteAdapters({Platform.SLACK: adapter}, _primary_profile_routes_for_current_home())
    transport, error = _resolve_target_transport(
        {"id": "brief"}, Platform.SLACK, "slack", {"platform": "slack", "chat_id": "C12345678"},
        shared, satellite)
    assert error is None and transport[2] is adapter
    for target in ("slack:C87654321", "slack:C12345678:123.456", "telegram:C12345678"):
        assert _preflight_check_delivery({"deliver": target}) is not None
    assert shared.get(Platform.SLACK, {"chat_id": "C87654321"}) is None
    assert shared.get(Platform.SLACK, {"chat_id": "C12345678", "thread_id": "123.456"}) is None
    assert shared.get(Platform.SLACK) is None
    config["cron"]["shared_delivery_targets"][0]["enabled"] = False
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    assert _preflight_check_delivery({"deliver": "slack:C12345678"}) is not None


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda record, entry: record.update(updated_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat()), id="stale"),
    pytest.param(lambda record, entry: record.update(served_profiles=["default"]), id="unserved"),
    pytest.param(lambda record, entry: record["platforms"]["slack"].update(state="disconnected"), id="disconnected"),
    pytest.param(lambda record, entry: record.update(pid=456), id="wrong-pid"),
    pytest.param(lambda record, entry: entry.update(profile="sales"), id="wrong-profile"),
    pytest.param(lambda record, entry: entry.pop("chat_id"), id="broad"),
    pytest.param(lambda record, entry: entry.update(user_id="some-user"), id="inbound-only"),
])
def test_output_grant_needs_live_serving_primary_and_exact_scope(shared_home, mutate):
    root, record, config, _ = shared_home
    mutate(record, config["cron"]["shared_delivery_targets"][0])
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    assert _preflight_check_delivery({"deliver": "slack:C12345678"}) is not None
    shared = SharedRouteAdapters({Platform.SLACK: object()}, _primary_profile_routes_for_current_home())
    assert shared.get(Platform.SLACK, {"chat_id": "C12345678"}) is None


def test_explicit_thread_grant_does_not_authorize_channel_or_another_thread(shared_home):
    root, _, config, _ = shared_home
    config["cron"]["shared_delivery_targets"][0]["thread_id"] = "123.456"
    (root / "config.yaml").write_text(yaml.safe_dump(config))
    assert _preflight_check_delivery({"deliver": "slack:C12345678:123.456"}) is None
    assert _preflight_check_delivery({"deliver": "slack:C12345678"}) is not None
    assert _preflight_check_delivery({"deliver": "slack:C12345678:999.000"}) is not None
