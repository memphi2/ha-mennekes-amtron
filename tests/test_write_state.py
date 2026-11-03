"""Reloads and cold starts must not erase wallbox write protection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu import registers as R
from custom_components.mennekes_amtron_rtu.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.client_errors import AmtronRateLimitedError
from custom_components.mennekes_amtron_rtu.const import CONTROL_MODE_MASTER, DOMAIN
from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.enums import RequestedPhases
from tests.fakes import FakeModbusClient, device_bank
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_issues


@pytest.mark.parametrize("resume_action", ["async_resume_charging", "async_recover_from_error"])
@pytest.mark.parametrize("new_cap", [None, 6])
def test_cold_start_and_real_entry_reload_preserve_write_guards_and_setpoint(
    monkeypatch, resume_action, new_cap
):
    async def run():
        clock = [1000.0]
        hass = FakeHass()
        entry = FakeConfigEntry(options={"control_mode": CONTROL_MODE_MASTER})
        transport = FakeModbusClient(device_bank(charging_current=8))

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
        with pytest.raises(ServiceValidationError):
            await runtime.control.async_pause_charging()
        with pytest.raises(ServiceValidationError):
            await runtime.control.async_set_requested_phases(RequestedPhases.SINGLE_PHASE)

        clock[0] += 300
        await runtime.control.async_pause_charging()
        assert runtime.control.charging_setpoint == 8
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
        assert runtime.control.charging_setpoint == 8
        with pytest.raises(AmtronRateLimitedError):
            await runtime.client.async_write_register(R.CHARGING_CURRENT_EMS, 8, min_interval=5)
        clock[0] += 5
        runtime.coordinator.data = WallboxData(values={"charging_current_ems": 1.0})
        with pytest.raises(ServiceValidationError) as error:
            await runtime.control.async_resume_charging()
        assert error.value.translation_placeholders == {"seconds": "294"}
        clock[0] += 294
        await getattr(runtime.control, resume_action)()
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == (new_cap or 8)
        assert await integration.async_unload_entry(hass, entry)
        monkeypatch.setattr(integration.ir, "async_get", lambda _: SimpleNamespace(issues={}))
        await integration.async_remove_entry(hass, entry)
        assert entry.entry_id not in hass.data[f"{DOMAIN}_write_states"]

    asyncio.run(run())
