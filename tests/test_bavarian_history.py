"""BavarianData's local public history yields the complete charges and their levels."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata import history
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.history import (
    async_read_records,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.identity import (
    bavarian_vehicle_vin,
)
from custom_components.mennekes_amtron_rtu.vehicle_sources.types import ChargeRecord

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


SPAN = {"since": BASE, "until": BASE + 1200}
FOUND = (ChargeRecord(BASE + 102, BASE + 1002, 36, 50),)


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def record(**changes):
    return {
        "vin": "WBA12345678901234", "start": iso(BASE + 102),
        "end": iso(BASE + 1002), "energy_kwh": 18.0, "grid_kwh": 20.5,
        "soc_start": 36, "soc_end": 50,
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


def read(hass, device="car", **kwargs):
    return asyncio.run(async_read_records(hass, device, **{**SPAN, **kwargs}))


def query(rows, **kwargs):
    return read(fake_hass({"sessions": rows}), **kwargs)


def test_public_service_contract_uses_local_history_and_battery_energy():
    hass = fake_hass({"sessions": [record()]})
    assert read(hass, "selected-device", home_zone="Home") == FOUND
    hass.services.async_call.assert_awaited_once_with(
        "bavariandata", "get_charging_sessions",
        {
            "device_id": "selected-device", "from": iso(BASE),
            "vin": VIN,
            "to": iso(BASE + 1200), "limit": 50,
        },
        blocking=True, return_response=True,
    )


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
            assert await async_read_records(hass, "car", **SPAN) == FOUND
            assert received[0]["device_id"] == "car"
            assert not hass.services.has_service("bavariandata", "fetch_charging_history")
        finally:
            await hass.async_stop()

    asyncio.run(run())


@pytest.mark.parametrize(("available", "support"), [
    (False, SupportsResponse.NONE), (True, SupportsResponse.NONE),
])
def test_absent_history_capability_is_unknown_without_calling_any_service(available, support):
    hass = fake_hass({}, available=available, supports=support)
    assert read(hass) is None
    hass.services.async_call.assert_not_called()


def test_response_optional_service_is_also_compatible():
    hass = fake_hass({"sessions": [record()]}, supports=SupportsResponse.OPTIONAL)
    assert read(hass) == FOUND


@pytest.mark.parametrize("device_id", [None, False, "", " ", "x" * 257])
def test_invalid_device_selection_does_not_call_services(device_id):
    hass = fake_hass({})
    assert read(hass, device_id) is None
    hass.services.async_call.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"home_zone": False}, {"home_zone": ""}, {"home_zone": " "},
    {"since": BASE, "until": BASE}, {"since": BASE + 1, "until": BASE},
])
def test_invalid_home_zone_or_empty_span_is_not_queried(kwargs):
    hass = fake_hass({})
    assert read(hass, **kwargs) is None
    hass.services.async_call.assert_not_called()


@pytest.mark.parametrize("payload", [
    None, [], {}, {"sessions": {}}, {"sessions": [None]},
])
def test_missing_or_invalid_history_response_is_unknown(payload):
    assert read(fake_hass(payload)) is None


def test_an_empty_history_has_no_charge():
    assert query([]) == ()


@pytest.mark.parametrize("count", [50, 51])
def test_full_history_page_cannot_prove_that_it_was_not_truncated(count):
    assert query([record()] * count) is None


def test_every_complete_record_comes_oldest_first():
    early = record(end=iso(BASE + 400), soc_start=20, soc_end=30)
    late = record(start=iso(BASE + 500), soc_start=30, soc_end=55.5)
    both = (ChargeRecord(BASE + 102, BASE + 400, 20, 30), ChargeRecord(BASE + 500, BASE + 1002, 30, 55.5))
    assert query([late, early]) == both
    assert query([early, late]) == both
    early["vin"] = early["vin"].lower()
    assert query([early]) == both[:1]


@pytest.mark.parametrize("changes", [
    {"late_start": True}, {"late_start": None}, {"late_start": 0},
    {"interrupted": True}, {"interrupted": None}, {"interrupted": 0},
    {"charge_method": "dc"}, {"charge_method": "unknown"},
    {"soc_start": None}, {"soc_end": None}, {"soc_start": "36"}, {"soc_end": True},
    {"soc_start": float("nan")}, {"soc_end": float("inf")}, {"soc_start": -1},
    {"soc_end": 101}, {"soc_end": 36}, {"soc_end": 30},
    {"start": None}, {"end": None}, {"start": ""}, {"end": "x" * 129},
    {"start": "2023-11-14T22:15:00"}, {"end": "invalid"},
    {"start": "1900-01-01T00:00:00+00:00"},
    {"end": "9999-12-31T23:59:59-23:00"},
    {"start": iso(BASE + 1002)},
    {"location": []}, {"location_assumed": 0},
])
def test_an_unusable_record_is_passed_over(changes):
    assert query([record(**changes)]) == ()
    older = record(start=iso(BASE + 10), end=iso(BASE + 90), soc_start=10, soc_end=12)
    newer = record(**{"start": iso(BASE + 102), **changes})
    assert query([newer, older]) == (ChargeRecord(BASE + 10, BASE + 90, 10, 12),)


@pytest.mark.parametrize("vin", [None, " ", "x" * 65, "WBA98765432109876"])
def test_another_or_unnamed_vehicle_in_the_answer_makes_it_unusable(vin):
    assert query([record(vin=vin)]) is None
    assert query([record(), record(vin=vin, start=iso(BASE + 2000), end=iso(BASE + 3000))]) is None


def test_the_cars_energy_figures_are_not_used():
    # Its battery energy follows lagging power reports, its grid energy is
    # another measurement: only the span and the levels count.
    item = record(energy_kwh=None, grid_kwh=None)
    assert query([item]) == FOUND


def test_optional_method_and_location_fields_are_not_invented():
    item = record()
    for key in ("charge_method", "location", "location_assumed"):
        item.pop(key)
    assert query([item], home_zone="My home") == FOUND


def test_known_away_location_rejects_but_assumed_location_is_not_evidence():
    assert query([record(location={"zone": "Office"})], home_zone="Home") == ()
    assert query([record(location={"zone": "Office"}, location_assumed=True)], home_zone="Home") == FOUND
    assert query([record(location={"zone": "Wohnung"})], home_zone="Wohnung") == FOUND
    assert query([record(location={"zone": "HOME"})], home_zone="Home") == FOUND
    assert query([record(location={"zone": "Home"})], home_zone="Zuhause") == ()
    assert query([record(location={"zone": None})], home_zone="Home") == FOUND
    assert query([record(location={"zone": 1})], home_zone="Home") == ()
    assert query([record(location={"zone": "Office"})]) == FOUND


def test_timezone_offsets_and_z_timestamps_identify_the_same_interval():
    item = record()
    item["start"] = datetime.fromisoformat(item["start"]).astimezone().isoformat()
    item["end"] = item["end"].replace("+00:00", "Z")
    assert query([item]) == FOUND


def test_public_service_errors_are_left_for_the_owner_to_report():
    hass = fake_hass({})
    hass.services.async_call.side_effect = HomeAssistantError("source unavailable")
    with pytest.raises(HomeAssistantError, match="source unavailable"):
        read(hass)


def test_history_service_timeout_is_bounded(monkeypatch):
    hass = fake_hass({})

    async def blocked(*_args, **_kwargs):
        await asyncio.Event().wait()

    hass.services.async_call.side_effect = blocked
    monkeypatch.setattr(history, "HISTORY_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(TimeoutError):
        read(hass)


def test_query_window_is_bounded_at_supported_timestamp_limits():
    hass = fake_hass({"sessions": []})
    read(hass, since=-5, until=1e12)
    data = hass.services.async_call.await_args.args[2]
    assert data["from"] == "1970-01-01T00:00:00+00:00"
    assert data["to"] == "9999-12-31T23:59:59+00:00"


@pytest.mark.parametrize("identifiers", [
    {("bavariandata", "account-entry")},
    {("another_integration", VIN)},
    {("bavariandata", VIN), ("bavariandata", "WBA98765432109876")},
    {("bavariandata", "not-a-vin")},
])
def test_account_debug_foreign_or_ambiguous_device_never_queries_history(identifiers):
    hass = fake_hass({"sessions": [record()]})
    hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers=identifiers)
    assert read(hass) is None
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
    assert read(hass, expected_vin="WBA98765432109876") is None
    hass.services.async_call.assert_not_called()


def test_device_rebinding_during_the_history_request_discards_the_response():
    hass = fake_hass({})

    async def rebound(*_args, **_kwargs):
        hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "WBA98765432109876")})
        return {"sessions": [record()]}

    hass.services.async_call.side_effect = rebound
    assert read(hass) is None
