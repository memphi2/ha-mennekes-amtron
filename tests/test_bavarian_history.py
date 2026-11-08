"""BavarianData's local public history must describe the whole wallbox charge."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.device.charging_session import (
    CompletedSession,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata import history
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.history import (
    HistoryMatch,
    async_read_history,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.identity import (
    bavarian_vehicle_vin,
)

BASE = 1700000000.0
VIN = "WBA12345678901234"


def vehicle_registry(*, identifiers=None, entries=None):
    """The public registry contract of a BavarianData vehicle device."""
    device = SimpleNamespace(
        identifiers={("bavariandata", VIN)} if identifiers is None else identifiers,
        config_entry_id="account-entry" if entries is None else next(iter(entries)),
    )
    return SimpleNamespace(
        async_get=Mock(return_value=device), devices={"car": device, "own": device},
    )


def session() -> CompletedSession:
    return CompletedSession(
        str(UUID(int=1)), BASE, BASE + 1200, BASE + 100, BASE + 105,
        BASE + 1000, BASE + 1005, 20,
    )


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def record(**changes):
    return {
        "vin": "WBA12345678901234", "start": iso(BASE + 102),
        "end": iso(BASE + 1002), "energy_kwh": 18.0, "grid_kwh": 20.5,
        "late_start": False, "interrupted": False, "charge_method": "ac",
        "location": {"zone": "Home"}, "location_assumed": False,
        **changes,
    }


def fake_hass(payload, *, available=True, supports=SupportsResponse.ONLY):
    return SimpleNamespace(data={dr.DATA_REGISTRY: vehicle_registry()}, services=SimpleNamespace(
        has_service=Mock(return_value=available),
        supports_response=Mock(return_value=supports),
        async_call=AsyncMock(return_value=payload),
    ))


def query(rows, **kwargs):
    return asyncio.run(async_read_history(fake_hass({"sessions": rows}), "car", session(), **kwargs))


def test_public_service_contract_uses_local_history_and_battery_energy():
    async def run():
        hass = fake_hass({"sessions": [record()]})
        result = await async_read_history(hass, "selected-device", session(), home_zone="Home")
        assert result == HistoryMatch(18, 1)
        hass.services.async_call.assert_awaited_once_with(
            "bavariandata", "get_charging_sessions",
            {
                "device_id": "selected-device", "from": iso(BASE - 60),
                "vin": VIN,
                "to": iso(BASE + 1260), "limit": 50,
            },
            blocking=True, return_response=True,
        )

    asyncio.run(run())


def test_real_ha_response_only_service_works_without_foreign_imports(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.data[dr.DATA_REGISTRY] = vehicle_registry()
        received = []

        async def respond(call):
            received.append(call.data)
            return {"sessions": [record()]}

        hass.services.async_register(
            "bavariandata", "get_charging_sessions", respond,
            supports_response=SupportsResponse.ONLY,
        )
        try:
            assert await async_read_history(hass, "car", session()) == HistoryMatch(18, 1)
            assert received[0]["device_id"] == "car"
            assert not hass.services.has_service("bavariandata", "fetch_charging_history")
        finally:
            await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize(("available", "support"), [
    (False, SupportsResponse.NONE), (True, SupportsResponse.NONE),
])
def test_absent_history_capability_is_unknown_without_calling_any_service(available, support):
    async def run():
        hass = fake_hass({}, available=available, supports=support)
        assert await async_read_history(hass, "car", session()) is None
        hass.services.async_call.assert_not_called()

    asyncio.run(run())


def test_response_optional_service_is_also_compatible():
    hass = fake_hass({"sessions": [record()]}, supports=SupportsResponse.OPTIONAL)
    assert asyncio.run(async_read_history(hass, "car", session())) == HistoryMatch(18, 1)


@pytest.mark.parametrize("device_id", [None, False, "", " ", "x" * 257])
def test_invalid_device_selection_does_not_call_services(device_id):
    hass = fake_hass({})
    assert asyncio.run(async_read_history(hass, device_id, session())) is None
    hass.services.async_call.assert_not_called()


@pytest.mark.parametrize("zone", [False, "", " "])
def test_invalid_home_zone_is_not_guessed(zone):
    hass = fake_hass({})
    assert asyncio.run(async_read_history(hass, "car", session(), home_zone=zone)) is None
    hass.services.async_call.assert_not_called()


@pytest.mark.parametrize("payload", [None, [], {}, {"sessions": {}}, {"sessions": []}])
def test_missing_or_invalid_history_response_is_unknown(payload):
    assert asyncio.run(async_read_history(fake_hass(payload), "car", session())) is None


@pytest.mark.parametrize("count", [50, 51])
def test_full_history_page_cannot_prove_that_it_was_not_truncated(count):
    assert query([record()] * count) is None


def test_records_for_pauses_are_sorted_summed_and_deduplicated():
    early = record(end=iso(BASE + 400), energy_kwh=7.0)
    late = record(start=iso(BASE + 500), energy_kwh=11.0)
    assert query([late, early, deepcopy(early)]) == HistoryMatch(18, 2)
    early["vin"] = early["vin"].lower()
    assert query([early, late]) == HistoryMatch(18, 2)


def test_touching_subsessions_do_not_overlap():
    assert query([
        record(end=iso(BASE + 500), energy_kwh=7),
        record(start=iso(BASE + 500), energy_kwh=11),
    ]) == HistoryMatch(18, 2)


def test_overlapping_or_conflicting_duplicate_records_are_ambiguous():
    assert query([record(), record(energy_kwh=17)]) is None
    assert query([
        record(end=iso(BASE + 600), energy_kwh=7),
        record(start=iso(BASE + 500), energy_kwh=11),
    ]) is None


def test_mixed_vehicles_are_not_summed_into_one_charge():
    assert query([
        record(end=iso(BASE + 400), energy_kwh=7),
        record(vin="WBA98765432109876", start=iso(BASE + 500), energy_kwh=11),
    ]) is None


@pytest.mark.parametrize("changes", [
    {"vin": None}, {"vin": " "}, {"vin": "x" * 65},
    {"late_start": True}, {"late_start": None}, {"late_start": 0},
    {"interrupted": True}, {"interrupted": None}, {"interrupted": 0},
    {"charge_method": "dc"}, {"charge_method": "unknown"},
    {"energy_kwh": None}, {"energy_kwh": 0}, {"energy_kwh": -1},
    {"energy_kwh": "18"}, {"energy_kwh": True}, {"energy_kwh": float("nan")},
    {"energy_kwh": float("inf")}, {"energy_kwh": 10**1000},
    {"start": None}, {"end": None}, {"start": ""}, {"end": "x" * 129},
    {"start": "2023-11-14T22:15:00"}, {"end": "invalid"},
    {"start": "1900-01-01T00:00:00+00:00"},
    {"end": "9999-12-31T23:59:59-23:00"},
    {"location": []}, {"location_assumed": 0},
])
def test_incomplete_or_malformed_overlapping_record_invalidates_the_group(changes):
    assert query([record(**changes)]) is None


def test_bad_subsession_is_not_silently_dropped_from_a_plausible_total():
    assert query([
        record(end=iso(BASE + 400), energy_kwh=7),
        record(start=iso(BASE + 500), energy_kwh=11, interrupted=True),
    ]) is None


def test_grid_energy_never_replaces_missing_battery_energy():
    item = record()
    item.pop("energy_kwh")
    assert item["grid_kwh"] > 0
    assert query([item]) is None


def test_optional_method_and_location_fields_are_not_invented():
    item = record()
    for key in ("charge_method", "location", "location_assumed"):
        item.pop(key)
    assert query([item], home_zone="My home") == HistoryMatch(18, 1)


def test_known_away_location_rejects_but_assumed_location_is_not_evidence():
    assert query([record(location={"zone": "Office"})], home_zone="Home") is None
    assert query([record(location={"zone": "Office"}, location_assumed=True)], home_zone="Home") == HistoryMatch(18, 1)
    assert query([record(location={"zone": "Wohnung"})], home_zone="Wohnung") == HistoryMatch(18, 1)
    assert query([record(location={"zone": "HOME"})], home_zone="Home") == HistoryMatch(18, 1)
    assert query([record(location={"zone": "Home"})], home_zone="Zuhause") is None
    assert query([record(location={"zone": None})], home_zone="Home") == HistoryMatch(18, 1)
    assert query([record(location={"zone": 1})], home_zone="Home") is None
    assert query([record(location={"zone": "Office"})]) == HistoryMatch(18, 1)


def test_timezone_offsets_and_z_timestamps_identify_the_same_interval():
    item = record()
    item["start"] = datetime.fromisoformat(item["start"]).astimezone().isoformat()
    item["end"] = item["end"].replace("+00:00", "Z")
    assert query([item]) == HistoryMatch(18, 1)


@pytest.mark.parametrize(("start", "end"), [
    (BASE + 39, BASE + 1000), (BASE + 166, BASE + 1000),
    (BASE + 100, BASE + 939), (BASE + 100, BASE + 1066),
    (BASE + 900, BASE + 100), (BASE - 61, BASE + 1000),
    (BASE + 100, BASE + 1261),
])
def test_whole_charge_must_fit_both_charge_boundaries_and_plug_window(start, end):
    assert query([record(start=iso(start), end=iso(end))]) is None


def test_matching_boundary_tolerance_is_inclusive():
    assert query([record(start=iso(BASE + 40), end=iso(BASE + 1065))]) == HistoryMatch(18, 1)
    assert query([record(start=iso(BASE + 165), end=iso(BASE + 940))]) == HistoryMatch(18, 1)


def test_definitely_outside_records_do_not_pollute_the_current_group():
    before = record(start=iso(BASE - 1000), end=iso(BASE - 61), energy_kwh=None)
    after = record(start=iso(BASE + 1261), end=None, interrupted=True)
    assert query([before, record(), after]) == HistoryMatch(18, 1)
    assert query([before, after]) is None
    assert query([None]) is None


def test_overflowing_battery_energy_sum_is_unknown():
    assert query([
        record(end=iso(BASE + 400), energy_kwh=1e308),
        record(start=iso(BASE + 500), energy_kwh=1e308),
    ]) is None


def test_public_service_errors_are_left_for_the_owner_to_report():
    async def run():
        hass = fake_hass({})
        hass.services.async_call.side_effect = HomeAssistantError("source unavailable")
        with pytest.raises(HomeAssistantError, match="source unavailable"):
            await async_read_history(hass, "car", session())

    asyncio.run(run())


def test_history_service_timeout_is_bounded(monkeypatch):
    async def run():
        hass = fake_hass({})

        async def blocked(*_args, **_kwargs):
            await asyncio.Event().wait()

        hass.services.async_call.side_effect = blocked
        monkeypatch.setattr(history, "HISTORY_TIMEOUT_SECONDS", 0.001)
        with pytest.raises(TimeoutError):
            await async_read_history(hass, "car", session())

    asyncio.run(run())


def test_query_window_is_bounded_at_supported_timestamp_limits():
    async def run():
        original = session()
        hass = fake_hass({"sessions": []})
        for boundary in (
            replace(original, connected_at=0),
            replace(original, disconnected_at=253402300799),
        ):
            await async_read_history(hass, "car", boundary)
        data = hass.services.async_call.await_args_list
        assert data[0].args[2]["from"] == "1970-01-01T00:00:00+00:00"
        assert data[1].args[2]["to"] == "9999-12-31T23:59:59+00:00"

    asyncio.run(run())


@pytest.mark.parametrize("identifiers", [
    {("bavariandata", "account-entry")},
    {("another_integration", VIN)},
    {("bavariandata", VIN), ("bavariandata", "WBA98765432109876")},
    {("bavariandata", "not-a-vin")},
])
def test_account_debug_foreign_or_ambiguous_device_never_queries_history(identifiers):
    hass = fake_hass({"sessions": [record()]})
    hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers=identifiers)
    assert asyncio.run(async_read_history(hass, "car", session())) is None
    hass.services.async_call.assert_not_called()


def test_missing_registry_removed_device_and_entry_identifier_are_not_vehicles():
    hass = fake_hass({})
    hass.data = {}
    assert bavarian_vehicle_vin(hass, "car") is None
    registry = vehicle_registry(entries={VIN})
    hass.data[dr.DATA_REGISTRY] = registry
    assert bavarian_vehicle_vin(hass, "car") is None
    registry.async_get.return_value = None
    assert bavarian_vehicle_vin(hass, "car") is None


def test_expected_vehicle_must_match_selected_registry_device():
    hass = fake_hass({"sessions": [record()]})
    assert asyncio.run(async_read_history(hass, "car", session(), expected_vin="WBA98765432109876")) is None
    hass.services.async_call.assert_not_called()


def test_foreign_vehicle_rows_are_rejected_even_when_outside_the_requested_interval():
    assert query([record(vin="WBA98765432109876")]) is None
    assert query([record(), record(vin="WBA98765432109876", start=iso(BASE + 2000), end=iso(BASE + 3000))]) is None


def test_device_rebinding_during_the_history_request_discards_the_response():
    async def run():
        hass = fake_hass({})
        async def rebound(*_args, **_kwargs):
            hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
            return {"sessions": [record()]}
        hass.services.async_call.side_effect = rebound
        assert await async_read_history(hass, "car", session()) is None
    asyncio.run(run())
