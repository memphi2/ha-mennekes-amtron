"""Logical profile selection keeps complete observed energy and prices."""

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronIdentityError,
)
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.fakes import FakeModbusClient, device_bank, put
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_client, _patch_issues

_CP = {EvseState.IDLE: CpState.A1, EvseState.CHARGING: CpState.C2}


def setup_owners(tmp_path, monkeypatch, *, costs=True):
    hass = FakeHass(str(tmp_path))
    transport = FakeModbusClient(device_bank())
    transport.bank[R.CP_STATE.address] = CpState.A1
    put(transport.bank, R.ENERGY_TOTAL, 100)
    _patch_client(monkeypatch, transport)
    _patch_issues(monkeypatch)
    monkeypatch.setattr(integration, "ChargeLossObserver", lambda *_: Mock(async_start=AsyncMock(), async_stop=AsyncMock()))
    entry = FakeConfigEntry(options={
        # The test polls the service itself, at the times it chooses.
        "scan_interval_seconds": 300,
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


def _host(hass, entry):
    return hass.data.get(f"{DOMAIN}_embedded", {}).get(entry.entry_id)


class Poller:
    """Let the service read the simulated wallbox, then HA read the service."""

    def __init__(self, hass, entry, transport):
        self._hass, self._entry, self._transport = hass, entry, transport
        self._at = time.time() + 1
        self._offset = 0.0

    async def __call__(self, state, energy):
        self._offset += 10
        at = self._at + self._offset
        self._transport.bank[R.EVSE_STATE.address] = state
        self._transport.bank[R.CP_STATE.address] = _CP[state]
        put(self._transport.bank, R.ENERGY_TOTAL, energy)
        service = _host(self._hass, self._entry)._service
        # Read every block now, whatever its cadence.
        service._poller._active_read_at = service._poller._slow_read_at = None
        with (
            patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", return_value=at),
            patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at),
        ):
            await service.refresh()
            await self._entry.runtime_data.coordinator.async_refresh()
        return self._entry.runtime_data.coordinator.data

    async def reconnect(self):
        """The bus link drops and comes back, as the Modbus library reports it."""
        _reconnect(_host(self._hass, self._entry)._service)
        await self._entry.runtime_data.coordinator.async_refresh()


def _reconnect(service):
    service.client._trace_connect(False)
    service.client._trace_connect(True)


async def _setup(hass, entry):
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    assert await integration.async_setup_entry(hass, entry)
    entry.state = ConfigEntryState.LOADED


async def _unload(hass, entry):
    assert await integration.async_unload_entry(hass, entry)
    entry.state = ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("costs", [False, True])
@pytest.mark.parametrize("already_charging", [False, True])
def test_unselected_profile_captures_baseline_and_survives_selection_reload(tmp_path, monkeypatch, costs, already_charging):
    async def run():
        hass, entry, transport = setup_owners(tmp_path, monkeypatch, costs=costs)
        await _setup(hass, entry)
        old = entry.runtime_data
        host = _host(hass, entry)
        poll = Poller(hass, entry, transport)
        await poll(EvseState.IDLE, 100)
        if already_charging:
            await poll(EvseState.CHARGING, 102)
        entry.options = {**entry.options, "active_vehicle_profile": "a" * 32}

        async def reload(_entry_id):
            await _unload(hass, entry)
            assert not old.client.connected
            await _setup(hass, entry)

        monkeypatch.setattr(hass.config_entries, "async_reload", reload)
        await integration.async_reload_entry(hass, entry)
        current = entry.runtime_data
        try:
            # The service, and with it the session, runs on through the reload.
            assert _host(hass, entry) is host
            assert current.charging_cost is old.charging_cost
            await poll(EvseState.CHARGING, 105)
            finished = await poll(EvseState.IDLE, 110)
            assert finished.charging_session["wallbox_kwh"] == 10
            if costs:
                assert current.charging_cost.last.result.complete
                assert current.charging_cost.last.result.known_cost == pytest.approx(4.0)
            assert not hass.data.get("mennekes_amtron_rtu_reload_clients")
        finally:
            await _unload(hass, entry)
        assert _host(hass, entry) is None
        assert not hass.data.get("mennekes_amtron_rtu_loss_attribution")
        if costs:
            assert current.charging_cost._closed
        assert not current.client.connected

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["skipped", "connection_changed", "identity_changed", "failed", "cost_changed", "disabled", "reconnected"])
def test_unclaimed_estimate_owners_are_closed_on_reload(tmp_path, monkeypatch, outcome):
    async def run():
        hass, entry, transport = setup_owners(tmp_path, monkeypatch)
        await _setup(hass, entry)
        old = entry.runtime_data
        host = _host(hass, entry)
        entry.options = {**entry.options, "active_vehicle_profile": "a" * 32}

        async def reload(_entry_id):
            await _unload(hass, entry)
            if outcome == "skipped":
                return
            if outcome == "connection_changed":
                entry.data = {**entry.data, "port": "/dev/other"}
            elif outcome == "identity_changed":
                # Swapping the wallbox means a new connection, which the
                # service verifies against the serial it is bound to.
                put(transport.bank, R.SERIAL_NUMBER, "NEW123456789")
                _reconnect(host._service)
                with pytest.raises(AmtronIdentityError):
                    await host._service.refresh()
            elif outcome == "failed":
                raise RuntimeError("replacement setup failed")
            elif outcome == "cost_changed":
                entry.options = {**entry.options, "charging_price": 0.5}
            elif outcome == "disabled":
                entry.options = {}
            elif outcome == "reconnected":
                _reconnect(host._service)
                await host._service.refresh()
            await _setup(hass, entry)

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
            # A reconnect is only a gap: a plain reload keeps the cost owner.
            assert old.charging_cost._closed is (outcome != "reconnected")
            assert not old.client.connected
            if outcome in {"skipped", "failed", "identity_changed"}:
                assert not hass.data.get("mennekes_amtron_rtu_loss_attribution")
                # Nothing took the service over; it is stopped.
                assert _host(hass, entry) is None
            elif outcome == "connection_changed":
                assert _host(hass, entry) is not host
            else:
                assert _host(hass, entry) is host
        finally:
            if entry.state is ConfigEntryState.LOADED:
                await _unload(hass, entry)

    asyncio.run(run())


def test_short_transport_reconnect_keeps_estimate_continuity(tmp_path, monkeypatch):
    async def run():
        hass, entry, transport = setup_owners(tmp_path, monkeypatch)
        await _setup(hass, entry)
        runtime = entry.runtime_data
        poll = Poller(hass, entry, transport)
        try:
            await poll(EvseState.IDLE, 100)
            await poll(EvseState.CHARGING, 105)
            await poll.reconnect()
            finished = await poll(EvseState.IDLE, 110)
            # The same wallbox and meter answer again: a short reconnect is a gap.
            assert finished.charging_session is not None
            await poll(EvseState.CHARGING, 115)
            finished = await poll(EvseState.IDLE, 120)
            assert finished.charging_session["wallbox_kwh"] == 10
            assert runtime.charging_cost.last.result.complete
        finally:
            await _unload(hass, entry)

    asyncio.run(run())
