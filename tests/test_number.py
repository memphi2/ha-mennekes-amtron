from __future__ import annotations

import asyncio

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MASTER,
)
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.energy_goal import MAX_ENERGY_GOAL_KWH
from custom_components.mennekes_amtron_rtu.number import async_setup_entry
from tests.fakes import device_bank
from tests.ha_fakes import FakeConfigEntry, build_runtime, collect


async def _number(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added["charging_current_limit"], entry, transport


def test_the_input_accepts_zero_for_no_limit() -> None:
    async def run() -> None:
        number, _entry, _transport = await _number()
        assert number.native_min_value == 0.0
        assert number.mode == NumberMode.SLIDER
        assert number.native_max_value == 16.0
        assert number.native_step == 0.1
        assert number.native_value == 16.0

    asyncio.run(run())


@pytest.mark.parametrize("value", [0.0, 6.0, 10.0])
def test_setting_a_value_writes_the_register(value: float) -> None:
    async def run() -> None:
        number, _entry, transport = await _number()
        await number.async_set_native_value(value)
        assert [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for address, words in transport.writes
            if address == R.CHARGING_CURRENT_EMS.address
        ] == [value]

    asyncio.run(run())


@pytest.mark.parametrize("remembered", [0.0, 12.0])
def test_while_paused_the_stored_setpoint_is_shown(remembered: float) -> None:
    async def run() -> None:
        number, entry, _transport = await _number(
            bank=device_bank(charging_current=1.0)
        )
        # The pause value of 1 A is no limit; the service's setpoint is shown.
        assert number.native_value == entry.service.control.charging_setpoint
        entry.service.control.charging_setpoint = remembered
        await entry.runtime_data.coordinator.async_refresh()
        assert number.native_value == remembered

    asyncio.run(run())


def test_unlimited_readback_shows_zero_instead_of_a_remembered_limit() -> None:
    async def run() -> None:
        number, entry, _transport = await _number(bank=device_bank(charging_current=0.0))
        assert number.native_value == 0.0
        entry.service.control.charging_setpoint = 12.0
        await entry.runtime_data.coordinator.async_refresh()
        assert number.native_value == 0.0

    asyncio.run(run())


@pytest.mark.parametrize("value", [0.1, 1.0, 5.9, -1.0, float("nan"), float("inf")])
def test_invalid_number_values_never_pause_the_device(value: float) -> None:
    async def run() -> None:
        number, _entry, transport = await _number()
        with pytest.raises(ServiceValidationError) as error:
            await number.async_set_native_value(value)
        assert error.value.translation_key == "charging_current_invalid"
        assert not transport.writes

    asyncio.run(run())


def test_zero_still_requires_write_access() -> None:
    async def run() -> None:
        number, _entry, transport = await _number(read_only=True)
        with pytest.raises(ServiceValidationError) as error:
            await number.async_set_native_value(0)
        assert error.value.translation_key == "read_only_mode"
        assert not transport.writes

    asyncio.run(run())


def test_a_float32_readback_is_shown_at_the_step_resolution() -> None:
    async def run() -> None:
        number, entry, _transport = await _number()
        entry.runtime_data.coordinator.data.values["charging_current_ems"] = 9.999999
        assert number.native_value == 10.0
        entry.runtime_data.coordinator.data.values["charging_current_ems"] = None
        assert number.native_value == entry.runtime_data.control.charging_setpoint

    asyncio.run(run())


def test_the_slider_ends_at_the_service_limit() -> None:
    async def run() -> None:
        number, entry, _transport = await _number(current_cap=10.0)
        assert number.native_max_value == 10.0
        assert entry.runtime_data.control.max_charging_current == 10.0

    asyncio.run(run())


async def _numbers(**kwargs: object) -> tuple[dict[str, object], object, object]:
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def test_the_energy_goal_is_kept_by_the_service_and_writes_nothing() -> None:
    async def run() -> None:
        numbers, entry, transport = await _numbers()
        goal = numbers["energy_goal"]
        assert set(numbers) == {"charging_current_limit", "energy_goal"}
        assert goal.available
        assert goal.native_value == 0.0
        assert goal.native_max_value == MAX_ENERGY_GOAL_KWH
        await goal.async_set_native_value(5.0)
        assert goal.native_value == 5.0
        assert entry.service.control.energy_goal.target_kwh == 5.0
        # A target limits additional energy; it never starts a charge.
        assert transport.writes == []
        with pytest.raises(ServiceValidationError) as error:
            await goal.async_set_native_value(-1.0)
        assert error.value.translation_key == "energy_goal_invalid"

        entry.runtime_data.client.snapshot["control"]["supports_energy_goal"] = False
        assert not goal.available

    asyncio.run(run())


class _VehicleSoc:
    def __init__(self) -> None:
        self.source: str | None = "sensor.car_soc"
        self.targets: list[float] = []

    async def async_set_target(self, value: float) -> None:
        self.targets.append(value)


def test_the_vehicle_target_exists_with_a_charge_level_and_follows_its_source() -> None:
    async def run() -> None:
        entry = FakeConfigEntry(options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "vehicle_soc_entity": "sensor.car_soc",
        })
        numbers, entry, _transport = await _numbers(entry=entry)
        target = numbers["soc_goal"]
        runtime = entry.runtime_data
        assert not target.available
        soc = _VehicleSoc()
        runtime.vehicle_soc = soc
        assert target.available
        assert target.native_value == 0.0
        assert target.native_max_value == 100.0
        await target.async_set_native_value(80.0)
        assert soc.targets == [80.0]
        soc.source = None
        assert not target.available

        runtime.vehicle_soc = None
        with pytest.raises(ServiceValidationError) as error:
            await target.async_set_native_value(80.0)
        assert error.value.translation_key == "soc_goal_unavailable"

    asyncio.run(run())


def test_the_vehicle_target_ends_at_the_cars_own_limit() -> None:
    async def run() -> None:
        entry = FakeConfigEntry(options={
            CONF_CONTROL_MODE: CONTROL_MODE_MASTER, "vehicle_soc_entity": "sensor.car_soc",
        })
        numbers, entry, _transport = await _numbers(entry=entry)
        # The last state the service reported, with the car's own limit.
        entry.runtime_data.client.soc_goal.maximum_percent = 90.0
        assert numbers["soc_goal"].native_max_value == 90.0

    asyncio.run(run())


def test_controls_are_unavailable_while_the_service_allows_no_writes() -> None:
    async def run() -> None:
        numbers, entry, _transport = await _numbers()
        assert all(entity.available for entity in numbers.values())
        entry.runtime_data.client.snapshot["control"]["read_only"] = True
        assert not any(entity.available for entity in numbers.values())

    asyncio.run(run())
