"""Timed charging options reject conflicting sources and save bounded inputs."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.options_flow import (
    _timed_errors,
    _timer_errors,
)
from tests.ha_fakes import FakeConfigEntry
from tests.test_charge_loss_options import Flow
from tests.test_options_flow import fields


@pytest.mark.parametrize(
    ("extra", "key", "error"),
    [
        (
            {"timed_capacity_entity": 123},
            "timed_capacity_entity",
            "timed_source_invalid",
        ),
        (
            {"timed_capacity_entity": "bad"},
            "timed_capacity_entity",
            "timed_source_invalid",
        ),
        (
            {"timed_capacity_entity": "switch.bad"},
            "timed_capacity_entity",
            "timed_source_invalid",
        ),
        (
            {"timed_calendar_title": "Trip"},
            "timed_calendar_title",
            "timed_calendar_required",
        ),
        (
            {"timed_calendar_entity": "calendar.car"},
            "timed_calendar_title",
            "timed_calendar_title_required",
        ),
        ({"timed_timer1_days": "mon"}, "timed_timer1_days", "timed_weekdays_invalid"),
        (
            {"timed_timer2_days": ["invalid"]},
            "timed_timer2_days",
            "timed_weekdays_invalid",
        ),
        (
            {"timed_vehicle_time_zone": "Mars/Olympus"},
            "timed_vehicle_time_zone",
            "timed_timezone_invalid",
        ),
        (
            {"timed_vehicle_time_zone": 9},
            "timed_vehicle_time_zone",
            "timed_timezone_invalid",
        ),
    ],
)
def test_invalid_timed_sources(extra, key, error):
    validator = (
        _timer_errors
        if key in {"timed_timer1_days", "timed_timer2_days", "timed_vehicle_time_zone"}
        else _timed_errors
    )
    assert validator(extra)[key] == error


@pytest.mark.parametrize(
    "capacity", [True, "75", float("nan"), float("inf"), 0, 251, 10**1000]
)
def test_invalid_capacity(capacity):
    assert (
        _timed_errors(
            {"vehicle_soc_entity": "sensor.soc", "timed_capacity_kwh": capacity}
        )["timed_capacity_kwh"]
        == "timed_capacity_invalid"
    )


@pytest.mark.parametrize("dynamic", [False, True])
def test_departure_manual_target_needs_only_capacity_and_saves_scoped(dynamic):
    async def run():
        entry = FakeConfigEntry(
            options={
                "vehicle_soc_entity": "sensor.soc",
                "charging_price": 0.3,
                "timed_calendar_entity": "calendar.old",
                "timed_calendar_title": "Old",
                "timed_bavarian_device": "car",
                "timed_timer1_days": ["mon"],
                "timed_departure_entity": "sensor.old",
                "timed_goal_reset_pending": True,
            }
        )
        flow = Flow(entry)
        assert (await flow.async_step_departure())["step_id"] == "departure"
        form = await flow.async_step_departure_configure()
        assert set(fields(form)) == {
            "capacity_source",
            "use_calendar",
            "use_departure_entity",
            "use_vehicle_timer",
        }
        result = await flow.async_step_departure_configure(
            {"capacity_source": "entity" if dynamic else "fixed"}
        )
        key = "timed_capacity_entity" if dynamic else "timed_capacity_kwh"
        assert set(fields(result)) == {key}
        method = (
            flow.async_step_departure_capacity_entity
            if dynamic
            else flow.async_step_departure_capacity_fixed
        )
        assert (await method({key: "sensor.capacity" if dynamic else 75}))[
            "step_id"
        ] == "review"
        saved = (await flow.async_step_save())["data"]
        assert saved[key] == ("sensor.capacity" if dynamic else 75)
        assert saved["timed_goal_enabled"] is True
        assert saved["timed_goal_reset_pending"] is True
        assert saved["timed_vehicle_provider"] == "none"
        assert saved["charging_price"] == 0.3
        assert (
            not {
                "timed_calendar_entity",
                "timed_calendar_title",
                "timed_bavarian_device",
                "timed_timer1_days",
                "timed_departure_entity",
            }
            & saved.keys()
        )
        entry.options = saved
        await flow.async_step_departure()
        await flow.async_step_departure_disable()
        disabled = (await flow.async_step_save())["data"]
        assert disabled["timed_goal_enabled"] is False
        assert disabled[key] == saved[key]

    asyncio.run(run())


def test_all_optional_departure_sources_coexist_and_generic_provider_saves():
    async def run():
        entry = FakeConfigEntry(
            options={
                "vehicle_soc_entity": "sensor.soc",
                "charge_loss_bavarian_device": "car",
            }
        )
        flow = Flow(entry)
        await flow.async_step_departure()
        await flow.async_step_departure_configure(
            {
                "capacity_source": "fixed",
                "use_calendar": True,
                "use_departure_entity": True,
                "use_vehicle_timer": True,
            }
        )
        assert (
            await flow.async_step_departure_capacity_fixed({"timed_capacity_kwh": 80})
        )["step_id"] == "departure_calendar"
        assert (
            await flow.async_step_departure_calendar(
                {
                    "timed_calendar_entity": "calendar.car",
                    "timed_calendar_title": "Trip",
                }
            )
        )["step_id"] == "departure_entity"
        assert (
            await flow.async_step_departure_entity(
                {"timed_departure_entity": "input_datetime.trip"}
            )
        )["step_id"] == "departure_vehicle"
        details = await flow.async_step_departure_vehicle(
            {"timed_vehicle_provider": "bavariandata"}
        )
        markers = {marker.schema: marker for marker in details["data_schema"].schema}
        assert markers["timed_vehicle_device"].description["suggested_value"] == "car"
        assert {
            "timed_vehicle_time_zone",
            "timed_timer1_days",
            "timed_timer2_days",
        } <= markers.keys()
        assert (
            await flow.async_step_departure_vehicle_details(
                {
                    "timed_vehicle_device": "car",
                    "timed_vehicle_time_zone": "Europe/Berlin",
                    "timed_timer1_days": ["mon"],
                    "timed_timer2_days": [],
                }
            )
        )["step_id"] == "review"
        saved = (await flow.async_step_save())["data"]
        assert saved["timed_vehicle_provider"] == "bavariandata"
        assert saved["timed_vehicle_device"] == "car"
        assert saved["timed_calendar_entity"] == "calendar.car"
        assert saved["timed_departure_entity"] == "input_datetime.trip"
        assert "timed_timer2_days" not in saved

    asyncio.run(run())


def test_departure_validates_every_selected_detail_before_review():
    async def run():
        flow = Flow(FakeConfigEntry(options={"vehicle_soc_entity": "sensor.soc"}))
        await flow.async_step_departure()
        assert (
            await flow.async_step_departure_configure({"capacity_source": "unknown"})
        )["errors"]
        await flow.async_step_departure_configure(
            {
                "capacity_source": "fixed",
                "use_calendar": True,
                "use_departure_entity": True,
                "use_vehicle_timer": True,
            }
        )
        assert (
            await flow.async_step_departure_capacity_fixed({"timed_capacity_kwh": 0})
        )["errors"]
        await flow.async_step_departure_capacity_fixed({"timed_capacity_kwh": 80})
        assert (await flow.async_step_departure_calendar({}))["errors"]
        await flow.async_step_departure_calendar(
            {"timed_calendar_entity": "calendar.car", "timed_calendar_title": "Trip"}
        )
        assert (
            await flow.async_step_departure_entity(
                {"timed_departure_entity": "switch.bad"}
            )
        )["errors"]
        await flow.async_step_departure_entity(
            {"timed_departure_entity": "sensor.trip"}
        )
        assert (
            await flow.async_step_departure_vehicle({"timed_vehicle_provider": "no"})
        )["errors"]
        await flow.async_step_departure_vehicle(
            {"timed_vehicle_provider": "bavariandata"}
        )
        assert (
            await flow.async_step_departure_vehicle_details(
                {
                    "timed_vehicle_device": "account",
                    "timed_timer1_days": ["no"],
                    "timed_vehicle_time_zone": "bad",
                }
            )
        )["errors"]
        assert (
            await flow.async_step_departure_vehicle_details(
                {"timed_vehicle_device": "car"}
            )
        )["step_id"] == "review"

    asyncio.run(run())


def test_departure_requires_soc_before_entering_and_again_before_save():
    async def run():
        entry = FakeConfigEntry()
        flow = Flow(entry)
        assert (await flow.async_step_departure())[
            "step_id"
        ] == "departure_requires_vehicle"
        assert (
            await flow.async_step_departure_configure({"capacity_source": "fixed"})
        )["step_id"] == "departure_requires_vehicle"
        entry.options["vehicle_soc_entity"] = "sensor.car"
        await flow.async_step_departure()
        await flow.async_step_departure_configure({"capacity_source": "fixed"})
        await flow.async_step_departure_capacity_fixed({"timed_capacity_kwh": 80})
        entry.options.clear()
        assert (await flow.async_step_save())["step_id"] == "options_changed"

    asyncio.run(run())


def test_departure_keeps_distinct_legacy_vehicle_and_empty_dynamic_source_is_error():
    async def run():
        flow = Flow(
            FakeConfigEntry(
                options={
                    "vehicle_soc_entity": "sensor.soc",
                    "timed_bavarian_device": "own",
                    "charge_loss_bavarian_device": "other",
                }
            )
        )
        await flow.async_step_departure()
        await flow.async_step_departure_configure(
            {"capacity_source": "entity", "use_vehicle_timer": True}
        )
        assert (await flow.async_step_departure_capacity_entity({}))["errors"]
        await flow.async_step_departure_capacity_entity(
            {"timed_capacity_entity": "sensor.capacity"}
        )
        details = await flow.async_step_departure_vehicle(
            {"timed_vehicle_provider": "bavariandata"}
        )
        marker = next(
            marker
            for marker in details["data_schema"].schema
            if marker.schema == "timed_vehicle_device"
        )
        assert marker.description["suggested_value"] == "own"

    asyncio.run(run())
