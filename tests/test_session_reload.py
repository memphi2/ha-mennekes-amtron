"""Logical profile selection keeps complete observed energy and prices."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.fakes import FakeModbusClient, device_bank, put
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_charging_session import reading
from tests.test_init import _patch_client, _patch_issues


def setup_owners(tmp_path, monkeypatch, *, costs=True):
    hass = FakeHass(str(tmp_path))
    transport = FakeModbusClient(device_bank())
    transport.bank[R.CP_STATE.address] = CpState.A1
    put(transport.bank, R.ENERGY_TOTAL, 100)
    _patch_client(monkeypatch, transport)
    _patch_issues(monkeypatch)
    monkeypatch.setattr(integration, "ChargeLossObserver", lambda *_: Mock(async_start=AsyncMock(), async_stop=AsyncMock()))
    entry = FakeConfigEntry(options={
        "vehicle_profiles": {"a" * 32: {
            "name": "Car", "provider": "none", "device_id": "", "options": {
                "charge_loss_source": "sensor",
                "vehicle_last_charge_energy_entity": "sensor.car_energy",
            },
        }},
        "active_vehicle_profile": None,
        "charging_cost_source": "fixed" if costs else "disabled",
        "charging_price": 0.4,
    })
    return hass, entry, transport


@pytest.mark.parametrize("costs", [False, True])
@pytest.mark.parametrize("already_charging", [False, True])
def test_unselected_profile_captures_baseline_and_survives_selection_reload(tmp_path, monkeypatch, costs, already_charging):
    async def run():
        hass, entry, transport = setup_owners(tmp_path, monkeypatch, costs=costs)
        assert await integration.async_setup_entry(hass, entry)
        old = entry.runtime_data
        assert old.sessions is not None
        if already_charging:
            transport.bank[R.EVSE_STATE.address] = EvseState.CHARGING
            transport.bank[R.CP_STATE.address] = CpState.C2
            put(transport.bank, R.ENERGY_TOTAL, 102)
            await old.coordinator.observe_session(reading(EvseState.CHARGING, 102))
        entry.options = {**entry.options, "active_vehicle_profile": "a" * 32}

        async def reload(_entry_id):
            assert await integration.async_unload_entry(hass, entry)
            assert old.client.connected
            assert await integration.async_setup_entry(hass, entry)

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        await integration.async_reload_entry(hass, entry)
        current = entry.runtime_data
        try:
            assert current.sessions is old.sessions
            assert current.charging_cost is old.charging_cost
            assert current.sessions._coordinator is current.coordinator
            await current.coordinator.observe_session(reading(EvseState.CHARGING, 105))
            finished = reading(EvseState.IDLE, 110)
            await current.coordinator.observe_session(finished)
            assert finished.charging_session["wallbox_kwh"] == 10
            if costs:
                assert current.charging_cost.last.result.complete
                assert current.charging_cost.last.result.known_cost == pytest.approx(4.0)
            assert not hass.data.get("mennekes_amtron_rtu_reload_clients")
        finally:
            await integration.async_unload_entry(hass, entry)
        assert current.sessions._closed
        assert not hass.data.get("mennekes_amtron_rtu_loss_attribution")
        if costs:
            assert current.charging_cost._closed
        assert not current.client.connected

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["skipped", "connection_changed", "identity_changed", "failed", "cost_changed", "disabled", "reconnected"])
def test_unclaimed_estimate_owners_are_closed_on_reload(tmp_path, monkeypatch, outcome):
    async def run():
        hass, entry, transport = setup_owners(tmp_path, monkeypatch)
        assert await integration.async_setup_entry(hass, entry)
        old = entry.runtime_data
        entry.options = {**entry.options, "active_vehicle_profile": "a" * 32}

        async def reload(_entry_id):
            assert await integration.async_unload_entry(hass, entry)
            if outcome == "skipped":
                return
            if outcome == "connection_changed":
                entry.data = {**entry.data, "port": "/dev/other"}
            elif outcome == "identity_changed":
                put(transport.bank, R.SERIAL_NUMBER, "NEW123456789")
            elif outcome == "failed":
                raise RuntimeError("replacement setup failed")
            elif outcome == "cost_changed":
                entry.options = {**entry.options, "charging_price": 0.5}
            elif outcome == "disabled":
                entry.options = {}
            elif outcome == "reconnected":
                await old.client.async_close()
            assert await integration.async_setup_entry(hass, entry)

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        # This test is about reload handoff, not the in-place vehicle switch.
        monkeypatch.setattr(integration, "_async_switch_vehicle", AsyncMock(return_value=False))
        try:
            if outcome == "failed":
                with pytest.raises(RuntimeError, match="replacement setup failed"):
                    await integration.async_reload_entry(hass, entry)
            elif outcome == "identity_changed":
                with pytest.raises(ConfigEntryNotReady):
                    await integration.async_reload_entry(hass, entry)
            else:
                await integration.async_reload_entry(hass, entry)
            assert not hass.data.get("mennekes_amtron_rtu_reload_clients")
            assert old.charging_cost._closed
            assert old.sessions._closed is (outcome != "cost_changed")
            if outcome in {"skipped", "failed", "connection_changed", "identity_changed"}:
                assert not old.client.connected
            if outcome in {"skipped", "failed", "identity_changed"}:
                assert not hass.data.get("mennekes_amtron_rtu_loss_attribution")
        finally:
            if entry.runtime_data is not old:
                await integration.async_unload_entry(hass, entry)

    asyncio.run(run())


def test_transport_reconnect_during_a_poll_invalidates_estimate_continuity(tmp_path, monkeypatch):
    async def run():
        hass, entry, _transport = setup_owners(tmp_path, monkeypatch)
        assert await integration.async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        try:
            await runtime.coordinator.observe_session(reading(EvseState.CHARGING, 105))
            runtime.client._trace_connect(False)
            runtime.client._trace_connect(True)
            finished = reading(EvseState.IDLE, 110)
            await runtime.coordinator.observe_session(finished)
            assert finished.charging_session is None
            assert runtime.charging_cost.last.result is None
            await runtime.coordinator.observe_session(reading(EvseState.CHARGING, 115))
            finished = reading(EvseState.IDLE, 120)
            await runtime.coordinator.observe_session(finished)
            assert finished.charging_session["wallbox_kwh"] == 10
            assert runtime.charging_cost.last.result.complete
        finally:
            await integration.async_unload_entry(hass, entry)

    asyncio.run(run())
