"""Every settings area names the entities it reads."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.core import State

from custom_components.mennekes_amtron_rtu.vehicle_profiles import profiles
from custom_components.mennekes_amtron_rtu.vehicle_sources.bavariandata.detection import (
    CHARGING_CABLE,
)
from tests.ha_fakes import FakeConfigEntry
from tests.test_bavarian_history import VIN
from tests.test_options_flow import fields
from tests.test_vehicle_profile_options import Flow, open_profile
from tests.test_vehicle_profiles import A, profile_options


def placeholders(result):
    return {key: value for key, value in result["description_placeholders"].items() if key != "vehicle"}


@pytest.mark.parametrize(("feature", "expected"), [
    ({"vehicle_soc_use_profile": True, "vehicle_limit_use_profile": True},
     {"soc_entity": "sensor.test_0", "limit_entity": "sensor.test_1"}),
    ({"vehicle_soc_entity": "sensor.soc", "vehicle_charge_limit_entity": "sensor.limit"},
     {"soc_entity": "sensor.soc", "limit_entity": "sensor.limit"}),
    ({}, {"soc_entity": "—", "limit_entity": "—"}),
])
def test_the_charge_level_area_names_its_entities(feature, expected):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(feature=feature)))
        await open_profile(flow)
        assert placeholders(await flow.async_step_vehicle_battery()) == expected

    asyncio.run(run())


@pytest.mark.parametrize(("feature", "expected"), [
    ({"timed_use_vehicle_capacity": True, "timed_calendar_entity": "calendar.trips",
      "timed_departure_entity": "input_datetime.leave", "timed_vehicle_provider": "profile"},
     {"departure_entity": "calendar.trips, input_datetime.leave, sensor.test_3",
      "capacity_entity": "sensor.test_2"}),
    ({"timed_capacity_entity": "sensor.capacity"},
     {"departure_entity": "—", "capacity_entity": "sensor.capacity"}),
    ({"timed_capacity_kwh": 77.5}, {"departure_entity": "—", "capacity_entity": "77.5 kWh"}),
])
def test_the_departure_area_names_its_entities(feature, expected):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(
            feature={"vehicle_soc_entity": "sensor.soc", **feature}
        )))
        await open_profile(flow)
        assert placeholders(await flow.async_step_departure()) == expected

    asyncio.run(run())


@pytest.mark.parametrize(("feature", "expected"), [
    ({"charge_loss_source": "vehicle_history", "charge_loss_vehicle_provider": "profile"},
     "bavariandata.get_charging_sessions"),
    ({"charge_loss_source": "sensor", "vehicle_last_charge_energy_entity": "sensor.energy"},
     "sensor.energy"),
    ({"charge_loss_source": "sensor"}, "—"),
    ({}, "—"),
])
def test_the_loss_area_names_its_source(feature, expected):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(feature=feature)))
        await open_profile(flow)
        assert placeholders(await flow.async_step_losses()) == {"loss_entity": expected}

    asyncio.run(run())


@pytest.mark.parametrize(("provider", "detection", "expected"), [
    ("bavariandata", None, ("—", "—")),
    ("none", {"source": "entities", "connected_entity": "binary_sensor.plug", "home_entity": "person.me"},
     ("binary_sensor.plug", "person.me")),
    # The car offers no plug or location entity here.
    ("bavariandata", {"source": "profile"}, ("—", "—")),
])
def test_the_detection_area_names_its_entities(provider, detection, expected):
    async def run():
        options = profile_options(provider=provider)
        if detection is not None:
            options["vehicle_profiles"][A]["detection"] = detection
        flow = Flow(FakeConfigEntry(options=options))
        await open_profile(flow)
        form = await flow.async_step_vehicle_detection_signals()
        assert placeholders(form) == {"plug_entity": expected[0], "location_entity": expected[1]}

    asyncio.run(run())


def test_a_new_car_uses_its_detection_signals_and_names_them():
    async def run():
        entry = FakeConfigEntry(options=profile_options(provider="none"))
        flow = Flow(entry)
        registry = flow.hass.data["entity_registry"]
        for unique, entity_id in ((f"{VIN}_{CHARGING_CABLE}", "binary_sensor.car_plug"),
                                  (f"{VIN}_tracker", "device_tracker.car")):
            registry.entries[unique] = SimpleNamespace(entity_id=entity_id, device_id="car", disabled_by=None)
        await flow.async_step_vehicle()
        await flow.async_step_vehicle_profile_add(
            {"vehicle_profile_name": "My BMW", "vehicle_provider": "bavariandata"}
        )
        await flow.async_step_vehicle_profile_device({"vehicle_device": "car"})
        for _ in range(3):
            form = await flow.async_step_wizard_next()
        assert form["step_id"] == "vehicle_detection_source"
        assert placeholders(form) == {
            "plug_entity": "binary_sensor.car_plug", "location_entity": "device_tracker.car",
        }
        saved = (await flow.async_step_vehicle_detection_source({"vehicle_detection_source": "profile"}))["data"]
        added = next(item for item in profiles(saved).values() if item.name == "My BMW")
        assert added.detection is not None
        assert added.detection.source == "profile"

    asyncio.run(run())


def without_timers(flow, keep=()):
    entries = flow.hass.data["entity_registry"].entries
    for unique in [key for key in entries if ".climate.timers." in key and not key.endswith(keep)]:
        entries.pop(unique)


@pytest.mark.parametrize("keep", [(), (".action",)])
def test_a_car_without_climate_timers_offers_no_departure_from_the_car(keep):
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(feature={"vehicle_soc_entity": "sensor.soc"})))
        # An incomplete timer is as good as none.
        without_timers(flow, keep)
        await open_profile(flow)
        await flow.async_step_departure()
        form = await flow.async_step_departure_configure()
        assert "use_vehicle_timer" not in fields(form)
        result = await flow.async_step_departure_configure({
            "capacity_source": "fixed", "use_calendar": False,
            "use_departure_entity": True, "use_vehicle_timer": True,
        })
        assert result["errors"] == {"use_vehicle_timer": "vehicle_capability_unavailable"}

    asyncio.run(run())


def test_departures_already_read_from_the_car_stay_visible():
    async def run():
        flow = Flow(FakeConfigEntry(options=profile_options(feature={
            "vehicle_soc_entity": "sensor.soc", "timed_goal_enabled": True,
            "timed_vehicle_provider": "profile",
        })))
        without_timers(flow)
        await open_profile(flow)
        menu = await flow.async_step_departure()
        assert placeholders(menu)["departure_entity"] == "bavariandata"
        form = await flow.async_step_departure_configure()
        assert "use_vehicle_timer" in fields(form)
        result = await flow.async_step_departure_configure({
            "capacity_source": "fixed", "use_calendar": False,
            "use_departure_entity": False, "use_vehicle_timer": True,
        })
        assert result["step_id"] == "departure_capacity_fixed"

    asyncio.run(run())


def test_a_car_that_reports_nothing_starts_without_settings():
    flow = Flow(FakeConfigEntry(options=profile_options(provider="none")))
    # A device that is no vehicle of the integration offers nothing.
    assert flow._vehicle_defaults("bavariandata", " ") == ({}, None)
    assert flow._vehicle_defaults("none", "") == ({}, None)


@pytest.mark.parametrize("limit", [None, "unavailable", "0"])
def test_a_car_without_a_reported_limit_keeps_targets_free(limit):
    flow = Flow(FakeConfigEntry(options=profile_options(provider="none")))
    if limit is not None:
        flow.states["sensor.test_1"] = State("sensor.test_1", limit, {"unit_of_measurement": "%"})
    options, _detection = flow._vehicle_defaults("bavariandata", "car")
    # The charge level is used, but a limit without a value would refuse
    # every target until the car reports one.
    assert options["vehicle_soc_use_profile"] is True
    assert options["vehicle_limit_use_profile"] is False
