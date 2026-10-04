from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.number import async_setup_entry
from tests.fakes import device_bank
from tests.ha_fakes import build_runtime, collect


async def _number(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added["charging_current_limit"], entry, transport


def test_the_slider_never_reaches_the_dangerous_values() -> None:
    async def run() -> None:
        number, _entry, _transport = await _number()
        assert number.native_min_value == 6.0
        assert number.native_max_value == 16.0
        assert number.native_step == 0.1
        assert number.native_value == 16.0

    asyncio.run(run())


def test_setting_a_value_writes_the_register() -> None:
    async def run() -> None:
        number, _entry, transport = await _number()
        await number.async_set_native_value(10.0)
        assert any(
            address == R.CHARGING_CURRENT_EMS.address
            for address, _ in transport.writes
        )

    asyncio.run(run())


def test_while_paused_the_stored_setpoint_is_shown() -> None:
    async def run() -> None:
        number, entry, _transport = await _number(
            bank=device_bank(charging_current=1.0)
        )
        assert number.native_value is None
        entry.runtime_data.control.charging_setpoint = 12.0
        assert number.native_value == 12.0

    asyncio.run(run())


def test_the_setpoint_is_restored_from_the_previous_run() -> None:
    async def run() -> None:
        number, entry, _transport = await _number()
        number.async_get_last_state = _last_state("11.0")
        await number.async_added_to_hass()
        assert entry.runtime_data.control.charging_setpoint == 11.0

    asyncio.run(run())


def test_a_restored_value_is_clamped_to_the_device_maximum() -> None:
    async def run() -> None:
        number, entry, _transport = await _number()
        number.async_get_last_state = _last_state("99.0")
        await number.async_added_to_hass()
        assert entry.runtime_data.control.charging_setpoint == 16.0

    asyncio.run(run())


def test_an_unusable_restored_state_is_ignored() -> None:
    async def run() -> None:
        number, entry, _transport = await _number()
        for state in (None, "unknown", "1.0"):
            entry.runtime_data.control.charging_setpoint = None
            number.async_get_last_state = _last_state(state)
            await number.async_added_to_hass()
            assert entry.runtime_data.control.charging_setpoint is None

    asyncio.run(run())


def test_an_existing_setpoint_is_not_overwritten() -> None:
    async def run() -> None:
        number, entry, _transport = await _number()
        entry.runtime_data.control.charging_setpoint = 7.0
        number.async_get_last_state = _last_state("15.0")
        await number.async_added_to_hass()
        assert entry.runtime_data.control.charging_setpoint == 7.0

    asyncio.run(run())


def _last_state(value: str | None):
    async def get_last_state() -> object:
        return None if value is None else SimpleNamespace(state=value)

    return get_last_state
