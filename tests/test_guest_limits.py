"""Guest permission expires with its connection and retains normal bus guards."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device.connection_sample import (
    connection_sample,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.guest_limits import (
    GuestLimits,
)
from custom_components.mennekes_amtron_rtu.device.power_window import (
    observed_phases,
)
from custom_components.mennekes_amtron_rtu.device.service_runtime import (
    ModbusService,
)
from tests import service_guest_fixture as runtime


@pytest.mark.parametrize(("values", "expected"), [
    ({"switched_phases": 1, "detected_ev_phases": 3}, 1),
    ({"switched_phases": 0, "grid_phases_connected": 0, "detected_ev_phases": 3}, 1),
    ({"switched_phases": 0, "grid_phases_connected": 2, "detected_ev_phases": 2}, 2),
    ({"detected_ev_phases": 3}, 0), ({"switched_phases": True}, 0),
    ({"switched_phases": 0, "grid_phases_connected": True, "detected_ev_phases": 3}, 0),
    ({"switched_phases": 0, "grid_phases_connected": 99, "detected_ev_phases": 3}, 0),
])
def test_power_learning_uses_selected_phases_not_vehicle_maximum(values, expected):
    data = WallboxData(values=values)
    assert (observed_phases(data.fresh) or 0) == expected
    data.held = frozenset({"switched_phases"})
    assert observed_phases(data.fresh) is None


def test_stopping_and_malformed_guest_requests_never_write(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            payload = {"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                       "expected_guest_revision":service.guest.revision}
            with pytest.raises(ValueError, match="unknown command field"):
                await service.command({**payload, "extra":True})
            with pytest.raises(ValueError, match="guest command"):
                await service.command({key: value for key, value in payload.items() if key != "expected_guest_revision"})
            service._stopping = True
            with pytest.raises(DeviceError, match="service_unavailable"):
                await service.command(payload)
            service._stopping = False
            assert not transport.writes
    asyncio.run(run())


def test_bus_owner_return_cannot_leave_a_healthy_looking_guest_loop(monkeypatch):
    async def run():
        service = ModbusService.__new__(ModbusService)
        monkeypatch.setattr(ModbusService, "_run_polling", AsyncMock())
        with pytest.raises(RuntimeError, match="bus owner stopped"):
            await service._run_bus()
    asyncio.run(run())


def test_long_unknown_connection_discards_guest_permission(tmp_path, monkeypatch):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            service.guest.scope = connection_sample(service).scope
            assert service.guest.scope is not None
            monkeypatch.setattr(ModbusService, "_poll", AsyncMock())
            monkeypatch.setattr(service, "_is_available", lambda: False)
            await service.refresh()
            assert service.guest.scope is not None
            cycle = service.client.write_state.plug_cycle
            cycle.read(None, service.identity.layout_version, now=0)
            cycle.read(None, service.identity.layout_version, now=61)
            await service.refresh()
            assert service.guest.scope is None
            assert service.guest.restore_pending
            assert cycle.reading is None
    asyncio.run(run())


@pytest.mark.parametrize("bus_error", [False, True])
def test_guest_limit_loop_retries_transient_errors_and_propagates_cancellation(monkeypatch, bus_error):
    from custom_components.mennekes_amtron_rtu.device import guest_limits
    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronConnectionError,
        AmtronRateLimitedError,
    )

    async def run():
        guest = GuestLimits(ModbusService.__new__(ModbusService))
        error = AmtronConnectionError("offline") if bus_error else AmtronRateLimitedError("current", 4)
        tick = AsyncMock(side_effect=[error, asyncio.CancelledError()])
        monkeypatch.setattr(guest, "tick", tick)
        monkeypatch.setattr(guest_limits.asyncio, "sleep", AsyncMock())
        with pytest.raises(asyncio.CancelledError):
            await guest.loop()
        assert tick.await_count == 2
    asyncio.run(run())
