"""Reloads and cold starts must not erase wallbox write protection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_issues


@pytest.mark.parametrize("resume_action", ["async_resume_charging", "async_recover_from_error"])
@pytest.mark.parametrize("new_cap", [None, 6])
@pytest.mark.parametrize("initial", [0, 8])
def test_cold_start_and_real_entry_reload_preserve_write_guards_and_setpoint(
    monkeypatch, resume_action, new_cap, initial
):
    async def run():
        clock = [1000.0]
        hass = FakeHass()
        entry = FakeConfigEntry(options={"control_mode": CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank(charging_current=initial, error_code=200))

        def factory(config, **kwargs):
            return MennekesModbusClient(config, client_factory=lambda _: transport,
                                      monotonic=lambda: clock[0], **kwargs)

        monkeypatch.setattr(integration, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        monkeypatch.setattr(integration, "MennekesModbusClient", factory)
        _patch_issues(monkeypatch)
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        runtime.control._monotonic = lambda: clock[0]
        runtime.control._request_refresh = None
        with pytest.raises(AmtronRateLimitedError):
            await runtime.client.async_write_register(R.CHARGING_CURRENT_EMS, 8, min_interval=5)
        await runtime.control.async_pause_charging()
        assert runtime.control.pending_action == "pause"
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == initial
        await runtime.control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert runtime.control.pending_action == "single_phase"

        clock[0] += 5
        # The short register guard has expired, but the cold-start transition
        # guard still defers the actual pause for another 295 seconds.
        runtime.client.check_write_interval(R.CHARGING_CURRENT_EMS, min_interval=5)
        await runtime.control.async_pause_charging()
        assert runtime.control.pending_action == "pause"
        assert runtime.control._mode_change_remaining() == 295
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == initial
        clock[0] += 295
        await runtime.control.async_pause_charging()
        assert runtime.control.pending_action is None
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == 1
        assert not any(address == R.REQUESTED_PHASES.address for address, _ in transport.writes)
        assert runtime.control.charging_setpoint == initial
        state = runtime.client.write_state
        assert await integration.async_unload_entry(hass, entry)
        if new_cap is not None:
            entry.options = {**entry.options, "current_limit": new_cap}
        clock[0] += 1
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        runtime.control._monotonic = lambda: clock[0]
        runtime.control._request_refresh = None
        assert runtime.client.write_state is state
        assert runtime.control.charging_setpoint == initial
        with pytest.raises(AmtronRateLimitedError):
            await runtime.client.async_write_register(R.CHARGING_CURRENT_EMS, 8, min_interval=5)
        clock[0] += 1
        runtime.coordinator.data = WallboxData(values={"charging_current_ems": 1.0})
        await runtime.control.async_resume_charging()
        assert runtime.control.pending_action == "resume"
        clock[0] += 3
        if resume_action == "async_resume_charging":
            # The shared transition guard survives the reload, too.
            assert runtime.control._mode_change_remaining() == 295
            clock[0] += 295
        await getattr(runtime.control, resume_action)()
        expected = new_cap or initial or 6
        if resume_action == "async_recover_from_error":
            expected = 1
            assert runtime.control.charging_setpoint == (
                min(initial, new_cap) if initial and new_cap else initial
            )
        elif initial == 0:
            expected = 0
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == expected
        assert await integration.async_unload_entry(hass, entry)
        monkeypatch.setattr(integration.ir, "async_get", lambda _: SimpleNamespace(issues={}))
        await integration.async_remove_entry(hass, entry)
        assert entry.entry_id not in hass.data[f"{DOMAIN}_write_states"]

    asyncio.run(run())
