"""Timed charging options reject conflicting sources and save bounded inputs."""

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.options_flow import (
    _departure_errors,
    _timer_errors,
)
from tests.ha_fakes import FakeConfigEntry
from tests.test_charge_loss_options import Flow
from tests.test_options_flow import fields


@pytest.mark.parametrize(
    ("extra", "key", "error"),
    [
        (
            {"departure_capacity_entity": 123},
            "departure_capacity_entity",
            "departure_source_invalid",
        ),
        (
            {"departure_capacity_entity": "bad"},
            "departure_capacity_entity",
            "departure_source_invalid",
        ),
        (
            {"departure_capacity_entity": "switch.bad"},
            "departure_capacity_entity",
            "departure_source_invalid",
        ),
        (
            {"departure_calendar_title": "Trip"},
            "departure_calendar_title",
            "departure_calendar_required",
        ),
        (
            {"departure_calendar_entity": "calendar.car"},
            "departure_calendar_title",
            "departure_calendar_title_required",
        ),
        ({"departure_timer1_days": "mon"}, "departure_timer1_days", "departure_weekdays_invalid"),
        (
            {"departure_timer2_days": ["invalid"]},
            "departure_timer2_days",
            "departure_weekdays_invalid",
        ),
        (
            {"departure_vehicle_time_zone": "Mars/Olympus"},
            "departure_vehicle_time_zone",
            "departure_timezone_invalid",
        ),
        (
            {"departure_vehicle_time_zone": 9},
            "departure_vehicle_time_zone",
            "departure_timezone_invalid",
        ),
    ],
)
def test_invalid_departure_sources(extra, key, error):
    validator = (
        _timer_errors
        if key in {"departure_timer1_days", "departure_timer2_days", "departure_vehicle_time_zone"}
        else _departure_errors
    )
    assert validator(extra)[key] == error


@pytest.mark.parametrize(
    "capacity", [True, "75", float("nan"), float("inf"), 0, 251, 10**1000]
)
def test_invalid_capacity(capacity):
    assert (
        _departure_errors(
            {"vehicle_soc_entity": "sensor.soc", "departure_capacity_kwh": capacity}
        )["departure_capacity_kwh"]
        == "departure_capacity_invalid"
    )


@pytest.mark.parametrize("dynamic", [False, True])
def test_departure_manual_target_needs_only_capacity_and_saves_scoped(dynamic):
    async def run():
        entry = FakeConfigEntry(
            options={
                "vehicle_soc_entity": "sensor.soc",
                "charging_price": 0.3,
                "departure_calendar_entity": "calendar.old",
                "departure_calendar_title": "Old",
                "departure_vehicle_provider": "bavariandata", "departure_vehicle_device": "car",
                "departure_timer1_days": ["mon"],
                "departure_entity": "sensor.old",
                "departure_reset_pending": True,
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
            "departure_minimum_soc",
        }
        result = await flow.async_step_departure_configure(
            {"capacity_source": "entity" if dynamic else "fixed", "departure_minimum_soc": 30}
        )
        key = "departure_capacity_entity" if dynamic else "departure_capacity_kwh"
        assert set(fields(result)) == {key}
        method = (
            flow.async_step_departure_capacity_entity
            if dynamic
            else flow.async_step_departure_capacity_fixed
        )
        assert result["last_step"] is True
        saved = (await method({key: "sensor.capacity" if dynamic else 75}))["data"]
        assert saved[key] == ("sensor.capacity" if dynamic else 75)
        assert saved["departure_enabled"] is True
        assert saved["departure_reset_pending"] is True
        assert saved["departure_vehicle_provider"] == "none"
        assert saved["departure_minimum_soc"] == 30
        assert saved["charging_price"] == 0.3
        assert (
            not {
                "departure_calendar_entity",
                "departure_calendar_title",
                "departure_vehicle_device",
                "departure_timer1_days",
                "departure_entity",
            }
            & saved.keys()
        )
        entry.options = saved
        # Zero switches the minimum level off again.
        again = Flow(entry)
        await again.async_step_departure()
        await again.async_step_departure_configure(
            {"capacity_source": "entity" if dynamic else "fixed", "departure_minimum_soc": 0}
        )
        assert "departure_minimum_soc" not in (await getattr(again, method.__name__)({key: saved[key]}))["data"]
        await flow.async_step_departure()
        disabled = (await flow.async_step_departure_disable())["data"]
        assert disabled["departure_enabled"] is False
        assert disabled[key] == saved[key]

    asyncio.run(run())


def test_all_optional_departure_sources_coexist_and_generic_provider_saves():
    async def run():
        entry = FakeConfigEntry(
            options={
                "vehicle_soc_entity": "sensor.soc",
                "charge_loss_vehicle_provider": "bavariandata", "charge_loss_vehicle_device": "car",
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
        capacity = await flow.async_step_departure_capacity_fixed()
        assert capacity["last_step"] is False
        assert (
            await flow.async_step_departure_capacity_fixed({"departure_capacity_kwh": 80})
        )["step_id"] == "departure_calendar"
        assert (
            await flow.async_step_departure_calendar(
                {
                    "departure_calendar_entity": "calendar.car",
                    "departure_calendar_title": "Trip",
                }
            )
        )["step_id"] == "departure_entity"
        assert (
            await flow.async_step_departure_entity(
                {"departure_entity": "input_datetime.trip"}
            )
        )["step_id"] == "departure_vehicle"
        details = await flow.async_step_departure_vehicle(
            {"departure_vehicle_provider": "bavariandata"}
        )
        markers = {marker.schema: marker for marker in details["data_schema"].schema}
        assert markers["departure_vehicle_device"].description["suggested_value"] == "car"
        assert {
            "departure_vehicle_time_zone",
            "departure_timer1_days",
            "departure_timer2_days",
        } <= markers.keys()
        saved_result = await flow.async_step_departure_vehicle_details(
                {
                    "departure_vehicle_device": "car",
                    "departure_vehicle_time_zone": "Europe/Berlin",
                    "departure_timer1_days": ["mon"],
                    "departure_timer2_days": [],
                }
            )
        saved = saved_result["data"]
        assert saved["departure_vehicle_provider"] == "bavariandata"
        assert saved["departure_vehicle_device"] == "car"
        assert saved["departure_calendar_entity"] == "calendar.car"
        assert saved["departure_entity"] == "input_datetime.trip"
        assert "departure_timer2_days" not in saved

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
            await flow.async_step_departure_capacity_fixed({"departure_capacity_kwh": 0})
        )["errors"]
        await flow.async_step_departure_capacity_fixed({"departure_capacity_kwh": 80})
        assert (await flow.async_step_departure_calendar({}))["errors"]
        await flow.async_step_departure_calendar(
            {"departure_calendar_entity": "calendar.car", "departure_calendar_title": "Trip"}
        )
        assert (
            await flow.async_step_departure_entity(
                {"departure_entity": "switch.bad"}
            )
        )["errors"]
        await flow.async_step_departure_entity(
            {"departure_entity": "sensor.trip"}
        )
        assert (
            await flow.async_step_departure_vehicle({"departure_vehicle_provider": "no"})
        )["errors"]
        await flow.async_step_departure_vehicle(
            {"departure_vehicle_provider": "bavariandata"}
        )
        assert (
            await flow.async_step_departure_vehicle_details(
                {
                    "departure_vehicle_device": "account",
                    "departure_timer1_days": ["no"],
                    "departure_vehicle_time_zone": "bad",
                }
            )
        )["errors"]
        assert (
            await flow.async_step_departure_vehicle_details(
                {"departure_vehicle_device": "car"}
            )
        )["type"] == "create_entry"

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
        entry.options.clear()
        assert (await flow.async_step_departure_capacity_fixed({"departure_capacity_kwh": 80}))["step_id"] == "options_changed"

    asyncio.run(run())


def test_departure_keeps_distinct_vehicle_and_empty_dynamic_source_is_error():
    async def run():
        flow = Flow(
            FakeConfigEntry(
                options={
                    "vehicle_soc_entity": "sensor.soc",
                    "departure_vehicle_provider": "bavariandata", "departure_vehicle_device": "own",
                    "charge_loss_vehicle_provider": "bavariandata", "charge_loss_vehicle_device": "other",
                }
            )
        )
        await flow.async_step_departure()
        await flow.async_step_departure_configure(
            {"capacity_source": "entity", "use_vehicle_timer": True}
        )
        assert (await flow.async_step_departure_capacity_entity({}))["errors"]
        await flow.async_step_departure_capacity_entity(
            {"departure_capacity_entity": "sensor.capacity"}
        )
        details = await flow.async_step_departure_vehicle(
            {"departure_vehicle_provider": "bavariandata"}
        )
        marker = next(
            marker
            for marker in details["data_schema"].schema
            if marker.schema == "departure_vehicle_device"
        )
        assert marker.description["suggested_value"] == "own"

    asyncio.run(run())
