"""Timed charging options reject conflicting sources and save bounded inputs."""
import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.config_schemas import options_schema
from custom_components.mennekes_amtron_rtu.options_flow import _timed_errors
from tests.ha_fakes import FakeConfigEntry
from tests.test_options_flow import Flow


@pytest.mark.parametrize(("extra", "key", "error"), [
    ({"timed_capacity_entity": 123}, "timed_capacity_entity", "timed_source_invalid"),
    ({"timed_capacity_entity": "bad"}, "timed_capacity_entity", "timed_source_invalid"),
    ({"timed_capacity_entity": "switch.bad"}, "timed_capacity_entity", "timed_source_invalid"),
    ({"timed_capacity_entity": "sensor.capacity", "timed_capacity_kwh": 75}, "timed_capacity_entity", "timed_capacity_ambiguous"),
    ({"timed_calendar_title": "Trip"}, "timed_calendar_title", "timed_calendar_required"),
    ({"timed_calendar_entity": "calendar.car"}, "timed_calendar_title", "timed_calendar_title_required"),
    ({"timed_timer1_days": "mon"}, "timed_timer1_days", "timed_weekdays_invalid"),
    ({"timed_timer2_days": ["invalid"]}, "timed_timer2_days", "timed_weekdays_invalid"),
    ({"timed_timer1_days": ["mon"]}, "timed_bavarian_device", "timed_bavarian_required"),
    ({"timed_vehicle_time_zone": "Mars/Olympus"}, "timed_vehicle_time_zone", "timed_timezone_invalid"),
    ({"timed_vehicle_time_zone": 9}, "timed_vehicle_time_zone", "timed_timezone_invalid"),
])
def test_invalid_timed_sources(extra, key, error):
    assert _timed_errors({"vehicle_soc_entity": "sensor.soc", **extra})[key] == error


@pytest.mark.parametrize("capacity", [True, "75", float("nan"), float("inf"), 0, 251])
def test_invalid_capacity(capacity):
    assert _timed_errors({"vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": capacity})["timed_capacity_kwh"] == "timed_capacity_invalid"


def test_timed_sources_require_vehicle_soc():
    assert _timed_errors({"timed_capacity_kwh": 75})["vehicle_soc_entity"] == "vehicle_soc_required"
    assert _timed_errors({}) == {}


@pytest.mark.parametrize("service", [False, True])
def test_timed_settings_roundtrip_with_calendar_and_fixed_capacity(service):
    async def run():
        entry = FakeConfigEntry(data={"server_url": "http://service:8765"} if service else None)
        flow = Flow(entry)
        values = options_schema({}, max_current=16, service_mode=service)({
            "vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": 75,
            "timed_calendar_entity": "calendar.car", "timed_calendar_title": "Trip",
        })
        result = await flow.async_step_init(values)
        assert result["data"]["timed_capacity_kwh"] == 75.0
        assert result["data"]["timed_calendar_title"] == "Trip"

    asyncio.run(run())


@pytest.mark.parametrize("service", [False, True])
def test_bmw_source_is_optional_and_coexists_with_calendar_and_timestamp(service):
    from tests.test_charge_loss_options import Flow as VehicleFlow

    async def run():
        flow = VehicleFlow(FakeConfigEntry(data={"server_url": "http://service:8765"} if service else None))
        values = options_schema({}, max_current=16, service_mode=service)({
            "vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": 75,
            "timed_calendar_entity": "calendar.car", "timed_calendar_title": "Trip",
            "timed_departure_entity": "sensor.departure",
            "timed_bavarian_device": "car", "timed_vehicle_time_zone": "Europe/Berlin",
            "timed_timer1_days": ["mon", "fri"], "timed_timer2_days": ["sat"],
        })
        result = await flow.async_step_init(values)
        assert result["type"] == "create_entry"
        assert result["data"]["timed_bavarian_device"] == "car"
        assert result["data"]["timed_calendar_entity"] == "calendar.car"
        assert result["data"]["timed_timer1_days"] == ["mon", "fri"]
        assert result["data"]["timed_vehicle_time_zone"] == "Europe/Berlin"
        # Removing the optional selection does not leave old BMW options behind.
        reset = await flow.async_step_init(options_schema({}, max_current=16, service_mode=service)({}))
        assert "timed_bavarian_device" not in reset["data"]
        assert "timed_timer1_days" not in reset["data"]
    asyncio.run(run())


def test_bmw_source_rejects_account_device():
    from homeassistant.helpers import device_registry as dr

    from tests.test_bavarian_history import vehicle_registry
    from tests.test_charge_loss_options import Flow as VehicleFlow

    async def run():
        flow = VehicleFlow(FakeConfigEntry())
        flow.hass.data[dr.DATA_REGISTRY] = vehicle_registry(identifiers={("bavariandata", "account-entry")})
        result = await flow.async_step_init({
            "vehicle_soc_entity": "sensor.soc", "timed_bavarian_device": "account",
        })
        assert result["errors"] == {"timed_bavarian_device": "timed_bavarian_invalid"}
    asyncio.run(run())
