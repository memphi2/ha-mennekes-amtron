"""Remote cost ownership survives logical reloads and cleans failed handoffs."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components import mennekes_amtron_rtu as integration
from custom_components.mennekes_amtron_rtu.data import RuntimeHandoff
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from tests.fakes import put
from tests.ha_fakes import FakeConfigEntry, FakeHass
from tests.test_init import _patch_issues
from tests.test_modbus_service import TOKEN, service_fixture


@pytest.mark.parametrize("failure", [False, True])
def test_remote_profile_reload_retains_costs_or_closes_failed_handoff(tmp_path, monkeypatch, failure):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        hass = FakeHass(str(tmp_path / "ha"))
        _patch_issues(monkeypatch)
        at = time.time() + 1
        poll_time = [1000.0]
        service._poller._monotonic = lambda: poll_time[0]
        service._poller._active_read_at = service._poller._slow_read_at = None

        async def observe(offset, energy, present):
            poll_time[0] = 1000.0 + offset
            service._poller._active_read_at = service._poller._slow_read_at = None
            transport.bank[R.EVSE_STATE.address] = EvseState.CHARGING if present else EvseState.IDLE
            transport.bank[R.CP_STATE.address] = CpState.C2 if present else CpState.A1
            put(transport.bank, R.ENERGY_TOTAL, energy)
            with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", return_value=at + offset):
                await service.refresh()

        try:
            async with TestServer(create_app(service)) as server, ClientSession() as session:
                monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
                entry = FakeConfigEntry(data={
                    "server_url": str(server.make_url("/")), "server_token": TOKEN,
                    "serial_number": service.identity.serial_number,
                }, options={
                    "charging_cost_source": "fixed", "charging_price": 0.4,
                    "vehicle_profiles": {
                        key * 32: {"name": name, "provider": "none", "device_id": "", "options": {}}
                        for key, name in (("a", "First"), ("b", "Second"))
                    },
                    "active_vehicle_profile": "a" * 32,
                })
                await observe(0, 100, False)
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at):
                    assert await integration.async_setup_entry(hass, entry)
                old = entry.runtime_data
                assert old.sessions is None
                # The connect snapshot is cached; establish the idle baseline
                # with the next independently observed service measurement.
                await observe(1, 100, False)
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 1):
                    await old.coordinator.async_refresh()
                assert old.charging_cost._anchored
                await observe(10, 102, True)
                with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 10):
                    await old.coordinator.async_refresh()
                assert old.charging_cost.current.complete
                # A profile edit, unlike a pure vehicle switch, reloads the entry.
                profiles = {key: dict(value) for key, value in entry.options["vehicle_profiles"].items()}
                profiles["b" * 32]["name"] = "Renamed"
                entry.options = {**entry.options, "vehicle_profiles": profiles, "active_vehicle_profile": "b" * 32}

                async def reload(_entry_id):
                    assert await integration.async_unload_entry(hass, entry)
                    assert not old.client.connected
                    assert not old.charging_cost._closed
                    if failure:
                        await server.close()
                    assert await integration.async_setup_entry(hass, entry)

                monkeypatch.setattr(hass.config_entries, "async_reload", reload)
                if failure:
                    with pytest.raises(ConfigEntryNotReady):
                        await integration.async_reload_entry(hass, entry)
                    assert old.charging_cost._closed
                else:
                    with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + 10):
                        await integration.async_reload_entry(hass, entry)
                    current = entry.runtime_data
                    try:
                        assert current.client is not old.client
                        assert current.charging_cost is old.charging_cost
                        for offset, energy, present in ((50, 105, True), (100, 110, False)):
                            await observe(offset, energy, present)
                            with patch("custom_components.mennekes_amtron_rtu.cost_observer.time.time", return_value=at + offset):
                                await current.coordinator.async_refresh()
                        assert current.charging_cost.last.result.complete
                        assert current.charging_cost.last.result.cost == pytest.approx(4.0)
                    finally:
                        await integration.async_unload_entry(hass, entry)
                    assert current.charging_cost._closed
                assert not hass.data.get("mennekes_amtron_rtu_reload_clients")
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("cancelled_owner", ["sessions", "charging_cost"])
def test_cancelled_handoff_cleanup_still_closes_all_remaining_owners(cancelled_owner):
    async def run():
        sessions = Mock(async_close=AsyncMock())
        cost = Mock(async_stop=AsyncMock())
        client = Mock(async_close=AsyncMock())
        if cancelled_owner == "sessions":
            sessions.async_close.side_effect = asyncio.CancelledError
        else:
            cost.async_stop.side_effect = asyncio.CancelledError
        handoff = RuntimeHandoff(identity=Mock(), client=client, sessions=sessions, charging_cost=cost)
        with pytest.raises(asyncio.CancelledError):
            await handoff.async_close()
        sessions.async_close.assert_awaited_once()
        cost.async_stop.assert_awaited_once()
        client.async_close.assert_awaited_once()
        assert handoff.client is handoff.sessions is handoff.charging_cost is None
        await handoff.async_close()
        client.async_close.assert_awaited_once()

    asyncio.run(run())
