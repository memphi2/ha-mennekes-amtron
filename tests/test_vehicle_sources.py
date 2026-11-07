"""Local provider capabilities preserve existing choices without vendor coupling."""

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.vehicle_sources import (
    HistoryMatch,
    ProviderSelection,
    loss_source,
    provider_definitions,
    provider_device_is_valid,
    provider_selection,
    resolve_departure_source,
    resolve_history_source,
)
from tests.test_bavarian_history import VIN, session, vehicle_registry

ADAPTER = "custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata"


def local_hass():
    return SimpleNamespace(
        data={dr.DATA_REGISTRY: vehicle_registry()},
        config=SimpleNamespace(time_zone="Europe/Berlin"),
        services=SimpleNamespace(async_call=AsyncMock()),
    )


def test_only_implemented_capabilities_are_advertised_and_vehicle_bound():
    for capability in ("departure", "charge_history"):
        definitions = provider_definitions(capability)
        assert len(definitions) == 1
        assert definitions[0].id == definitions[0].integration_domain == "bavariandata"
        assert definitions[0].capabilities == frozenset({"soc", "charge_limit", "capacity", "departure", "charge_history"})
        assert provider_selection({}, capability) == ProviderSelection("none", "")
    hass = local_hass()
    assert provider_device_is_valid(hass, "bavariandata", "car")
    assert not provider_device_is_valid(hass, "unsupported", "car")
    assert not provider_device_is_valid(hass, "none", "car")
    assert not provider_device_is_valid(hass, "bavariandata", None)


@pytest.mark.parametrize(("capability", "prefix", "legacy"), [
    ("departure", "timed", "timed_bavarian_device"),
    ("charge_history", "charge_loss", "charge_loss_bavarian_device"),
])
def test_explicit_generic_selection_never_revives_a_retained_legacy_device(capability, prefix, legacy):
    provider, device = f"{prefix}_vehicle_provider", f"{prefix}_vehicle_device"
    options = {legacy: "old-car"}
    assert provider_selection(options, capability) == ProviderSelection("bavariandata", "old-car")
    assert provider_selection({**options, provider: "none"}, capability) == ProviderSelection("none", "")
    assert provider_selection({**options, provider: "bavariandata"}, capability) == ProviderSelection("bavariandata", "")
    assert provider_selection({**options, device: "new-car"}, capability) == ProviderSelection("none", "new-car")
    assert provider_selection({**options, provider: "", device: 123}, capability) == ProviderSelection("none", "")
    assert provider_selection({**options, provider: 123}, capability) == ProviderSelection("none", "")
    assert provider_selection({legacy: 123}, capability) == ProviderSelection("none", "")


def test_history_and_departure_keep_independent_devices_and_generic_inputs_need_no_adapter():
    options = {
        "timed_bavarian_device": "departure-car", "charge_loss_bavarian_device": "history-car",
    }
    assert provider_selection(options, "departure").device_id == "departure-car"
    assert provider_selection(options, "charge_history").device_id == "history-car"
    options.update({"timed_vehicle_provider": "bavariandata", "timed_vehicle_device": "other-car"})
    assert provider_selection(options, "departure").device_id == "other-car"
    assert provider_selection(options, "charge_history").device_id == "history-car"
    generic = {
        "timed_departure_entity": "sensor.trip", "timed_calendar_entity": "calendar.trip",
        "vehicle_soc_entity": "sensor.soc", "timed_capacity_entity": "sensor.capacity",
    }
    # The baseline paths need neither an installed vehicle adapter nor its registry.
    assert resolve_departure_source(SimpleNamespace(), generic) is None
    assert resolve_history_source(SimpleNamespace(), generic) is None
    for provider in ("none", "unsupported", "bavariandata"):
        assert resolve_departure_source(SimpleNamespace(), {"timed_vehicle_provider": provider}) is None
        assert resolve_history_source(SimpleNamespace(), {"charge_loss_vehicle_provider": provider}) is None


@pytest.mark.parametrize(("options", "expected"), [
    ({}, "disabled"), ({"charge_loss_source": "bavariandata"}, "vehicle_history"),
    ({"charge_loss_source": "vehicle_history"}, "vehicle_history"),
    ({"charge_loss_source": "sensor"}, "sensor"), ({"charge_loss_source": 123}, "disabled"),
])
def test_loss_source_normalization_is_compatibility_only(options, expected):
    assert loss_source(options) == expected


@pytest.mark.parametrize(("zone", "days", "expected_zone", "expected_days"), [
    ("Europe/Paris", ["mon"], "Europe/Paris", ["mon"]),
    (123, "mon", "", ()),
    ("UTC", ["mon", 123], "UTC", ()),
])
def test_departure_adapter_binds_local_options_and_cannot_follow_a_rebound_vehicle(zone, days, expected_zone, expected_days):
    hass = local_hass()
    now = datetime(2026, 11, 7, tzinfo=UTC)
    options = {
        "timed_vehicle_provider": "bavariandata", "timed_vehicle_device": "car",
        "timed_vehicle_time_zone": zone, "timed_timer1_days": days,
    }
    source = resolve_departure_source(hass, options)
    assert source is not None
    assert source.available
    with patch(f"{ADAPTER}.read_bavarian_departure", return_value=now) as read:
        assert source.next_departure(now) == now
        read.assert_called_once_with(
            hass, "car", now=now, time_zone=expected_zone,
            timer1_days=expected_days, timer2_days=(),
        )
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
        assert not source.available
        assert source.next_departure(now) is None
        assert read.call_count == 1
    hass.services.async_call.assert_not_awaited()


def test_departure_falls_back_to_home_timezone_only_with_adapter_selected():
    hass = local_hass()
    source = resolve_departure_source(hass, {"timed_bavarian_device": "car"})
    assert source is not None
    now = datetime(2026, 11, 7, tzinfo=UTC)
    with patch(f"{ADAPTER}.read_bavarian_departure", return_value=None) as read:
        assert source.next_departure(now) is None
        assert read.call_args.kwargs["time_zone"] == "Europe/Berlin"


def test_history_adapter_preserves_statistics_identity_and_cleans_up_local_subscription(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        options = {"charge_loss_bavarian_device": "car"}
        old = resolve_history_source(hass, options)
        source = resolve_history_source(hass, {
            **options, "charge_loss_vehicle_provider": "bavariandata", "charge_loss_vehicle_device": "car",
        })
        assert old is not None
        assert source is not None
        assert source.source_key == old.source_key == f"bavariandata:car:{hashlib.sha256(VIN.encode()).hexdigest()}"
        assert VIN not in source.source_key
        assert source.available
        changed = Mock()
        unsub = source.subscribe(changed)
        try:
            hass.bus.async_fire("bavariandata_charging_stopped", {"unrelated": "car"})
            await hass.async_block_till_done()
            changed.assert_called_once_with()
            with patch(f"{ADAPTER}.async_read_history", return_value=HistoryMatch(18, 1)) as read:
                assert await source.async_match(session()) == HistoryMatch(18, 1)
                read.assert_awaited_once_with(hass, "car", session(), expected_vin=VIN)
                hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
                assert not source.available
                assert await source.async_match(session()) is None
                assert read.await_count == 1
        finally:
            unsub()
            hass.bus.async_fire("bavariandata_charging_stopped", {})
            await hass.async_block_till_done()
            assert changed.call_count == 1
            await hass.async_stop()
    asyncio.run(run())


def test_invalid_account_device_does_not_get_a_vehicle_identity():
    hass = local_hass()
    hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
    source = resolve_history_source(hass, {"charge_loss_bavarian_device": "account"})
    assert source is not None
    assert not source.available
    assert source.source_key == "bavariandata:account"
