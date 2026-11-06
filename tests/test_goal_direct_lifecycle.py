"""The HA owner restores a goal before recovery and cleans up its worker."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import (
    CONTROL_MODE_MASTER,
    DOMAIN,
)
from tests.fakes import FakeModbusClient, device_bank, put
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client, _patch_issues


def test_goal_survives_a_fresh_ha_owner_and_never_resumes(tmp_path, monkeypatch):
    async def run():
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(
            data={"port": "/dev/fake"}, options={"control_mode": CONTROL_MODE_MASTER},
        )
        hass = FakeHass(str(tmp_path))
        hass.state = CoreState.running
        assert await integration.async_setup_entry(hass, entry)
        first_control = entry.runtime_data.control
        first_control.pause_requested = True
        await first_control.async_set_energy_goal(1)
        baseline = first_control.energy_goal.baseline_kwh
        assert first_control.pause_requested is True
        assert not [write for write in transport.writes if write[0] == R.CHARGING_CURRENT_EMS.address]
        assert await integration.async_unload_entry(hass, entry)

        # New HA data and cold WriteState: only the durable file can restore it.
        restarted = FakeHass(str(tmp_path))
        restarted.state = CoreState.running
        assert await integration.async_setup_entry(restarted, entry)
        runtime = entry.runtime_data
        # FakeHass has no HA job runner; exercise the real coordinator directly.
        runtime.control._request_refresh = runtime.coordinator.async_refresh
        assert runtime.control is not first_control
        assert runtime.control.energy_goal.target_kwh == 1
        assert runtime.control.energy_goal.baseline_kwh == baseline
        assert runtime.control.energy_goal.status == "active"
        runtime.client.write_state.limiter.forget(R.CHARGING_CURRENT_EMS.key)
        put(transport.bank, R.ENERGY_TOTAL, baseline + 1)
        runtime.coordinator._poller._slow_read_at = None
        await runtime.coordinator.async_refresh()
        if task := runtime.goal._task:
            await asyncio.wait_for(task, 2)
        assert runtime.control.energy_goal.status == "reached"
        assert runtime.control.pause_requested is True
        assert any(write[0] == R.CHARGING_CURRENT_EMS.address for write in transport.writes)
        with pytest.raises(ServiceValidationError):
            await runtime.control.async_resume_charging()
        before = list(transport.writes)
        await runtime.control.async_set_energy_goal(0)
        assert runtime.control.pause_requested is True
        assert transport.writes == before
        assert await integration.async_unload_entry(restarted, entry)
        assert runtime.goal._stopping

    asyncio.run(run())


def test_corrupt_stored_goal_prevents_setup_without_starting_heartbeat(tmp_path, monkeypatch):
    async def run():
        transport = FakeModbusClient(device_bank())
        _patch_client(monkeypatch, transport)
        _patch_issues(monkeypatch)
        entry = FakeConfigEntry(data={"port": "/dev/fake"})
        hass = FakeHass(str(tmp_path))
        path = Path(hass.config.path(".storage", f"{DOMAIN}.energy_goal.{entry.entry_id}"))
        path.parent.mkdir(parents=True)
        await asyncio.to_thread(path.write_text, "{corrupt")
        with pytest.raises(ConfigEntryNotReady) as caught:
            await integration.async_setup_entry(hass, entry)
        assert caught.value.translation_key == "energy_goal_state_invalid"
        assert transport.writes == []
        assert not entry.runtime_data.client.connected
        assert await asyncio.to_thread(path.read_text) == "{corrupt"

    asyncio.run(run())
