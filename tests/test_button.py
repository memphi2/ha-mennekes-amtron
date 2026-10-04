from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron import registers as R
from custom_components.mennekes_amtron.button import async_setup_entry
from custom_components.mennekes_amtron.enums import EvseState
from tests.ha_fakes import build_runtime, collect


async def _buttons(**kwargs: object):
    hass, entry, transport = await build_runtime(**kwargs)
    added: dict[str, object] = {}
    await async_setup_entry(hass, entry, lambda items: added.update(collect(items)))
    return added, entry, transport


def test_the_recover_button_writes_the_documented_sequence() -> None:
    async def run() -> None:
        buttons, _entry, transport = await _buttons()
        await buttons["recover_from_error"].async_press()
        assert [address for address, _ in transport.writes] == [
            R.HEARTBEAT.address,
            R.CHARGING_RELEASE.address,
            R.CHARGING_CURRENT_EMS.address,
        ]

    asyncio.run(run())


def test_the_restart_button_refuses_outside_idle() -> None:
    async def run() -> None:
        buttons, entry, transport = await _buttons()
        entry.runtime_data.coordinator.data.values["evse_state"] = EvseState.CHARGING
        with pytest.raises(ServiceValidationError):
            await buttons["restart"].async_press()
        assert transport.writes == []

        entry.runtime_data.coordinator.data.values["evse_state"] = EvseState.IDLE
        await buttons["restart"].async_press()
        assert dict(transport.writes)[R.SYSTEM_RESTART.address] == [0xBB]

    asyncio.run(run())


def test_the_restart_button_needs_the_newest_layout() -> None:
    async def run() -> None:
        buttons, _entry, _transport = await _buttons(layout=R.LAYOUT_V01_02)
        assert "restart" not in buttons
        assert "recover_from_error" in buttons

    asyncio.run(run())
