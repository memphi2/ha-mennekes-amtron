"""Reloads and cold starts must not erase wallbox write protection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.const import (
    CONF_SCAN_INTERVAL_SECONDS,
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import RequestedPhases
from custom_components.mennekes_amtron_rtu.embedded import state_directory
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_issues


def _service(hass, entry):
    return hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service


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

        reloads = []

        async def reload(entry_id):
            assert entry_id == entry.entry_id
            reloads.append(entry_id)
            assert await integration.async_unload_entry(hass, entry)
            entry.state = ConfigEntryState.SETUP_IN_PROGRESS
            assert await integration.async_setup_entry(hass, entry)
            entry.state = ConfigEntryState.LOADED

        monkeypatch.setattr(service_runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        monkeypatch.setattr(service_runtime, "MennekesModbusClient", factory)
        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        _patch_issues(monkeypatch)
        assert await integration.async_setup_entry(hass, entry)
        entry.state = ConfigEntryState.LOADED
        service = _service(hass, entry)
        control = service.control
        control._monotonic = lambda: clock[0]
        control._request_refresh = None
        client = service.client
        with pytest.raises(AmtronRateLimitedError):
            await client.async_write_register(R.CHARGING_CURRENT_EMS, 8, min_interval=5)
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == initial
        await control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)
        assert control.pending_action == "single_phase"

        clock[0] += 5
        # The short register guard has expired, but the cold-start transition
        # guard still defers the actual pause for another 295 seconds.
        client.check_write_interval(R.CHARGING_CURRENT_EMS, min_interval=5)
        await control.async_pause_charging()
        assert control.pending_action == "pause"
        assert control._mode_change_remaining() == 295
        assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == initial
        clock[0] += 295
        await control.async_pause_charging()
        assert control.pending_action is None
        assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == 1
        assert not any(address == R.REQUESTED_PHASES.address for address, _ in transport.writes)
        assert control.charging_setpoint == initial
        state = client.write_state
        # Any changed option reloads the entry; only a new cap changes a limit.
        entry.options = {**entry.options, CONF_SCAN_INTERVAL_SECONDS: 10}
        if new_cap is not None:
            entry.options = {**entry.options, "current_limit": new_cap}
        clock[0] += 1
        # The simulated clock jumped past the service's freshness window, which
        # its own polling loop would have closed in real time.
        await service.refresh()
        await integration.async_reload_entry(hass, entry)
        assert reloads == [entry.entry_id]
        assert _service(hass, entry) is service
        assert service.client.write_state is state
        assert control.charging_setpoint == initial
        with pytest.raises(AmtronRateLimitedError):
            await client.async_write_register(R.CHARGING_CURRENT_EMS, 8, min_interval=5)
        clock[0] += 1
        service.data = WallboxData(values={"charging_current_ems": 1.0})
        await control.async_resume_charging()
        assert control.pending_action == "resume"
        clock[0] += 3
        if resume_action == "async_resume_charging":
            # The shared transition guard survives the reload, too.
            assert control._mode_change_remaining() == 295
            clock[0] += 295
        await getattr(control, resume_action)()
        expected = new_cap or initial or 6
        if resume_action == "async_recover_from_error":
            expected = 1
            assert control.charging_setpoint == (
                min(initial, new_cap) if initial and new_cap else initial
            )
        elif initial == 0:
            expected = 0
        assert await client.async_read_register(R.CHARGING_CURRENT_EMS) == expected
        assert await integration.async_unload_entry(hass, entry)
        monkeypatch.setattr(integration.ir, "async_get", lambda _: SimpleNamespace(issues={}))
        await integration.async_remove_entry(hass, entry)
        assert entry.entry_id not in hass.data[f"{DOMAIN}_embedded"]
        assert not state_directory(hass, entry.entry_id).exists()

    asyncio.run(run())
