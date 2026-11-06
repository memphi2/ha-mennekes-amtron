"""A lost reply must not restore an older current intent after service restart."""

from __future__ import annotations

import asyncio
import json

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests.fakes import raise_connection_error
from tests.test_modbus_service import current_writes, service_fixture


@pytest.mark.parametrize("outcome", ["lost_reply", "cancelled"])
def test_newer_uncertain_current_is_persisted_before_restart(tmp_path, outcome):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        release_flush = asyncio.Event()

        async def delayed_flush(_delay):
            await release_flush.wait()

        async def current(value):
            await service.command({
                "command": "set_current", "value": value,
                "expected_serial_number": service.identity.serial_number,
            })

        try:
            await service.heartbeat.async_stop()
            service.control._sleep = delayed_flush
            await current(8)
            await current(16)
            flush = service.control._flush_task
            assert json.loads(service.state_path.read_text())["charging_setpoint"] == 16
            clock[0] += 6
            original = transport.write_registers

            async def apply_without_reply(address, values, **kwargs):
                await original(address, values, **kwargs)
                if address == R.CHARGING_CURRENT_EMS.address:
                    if outcome == "cancelled":
                        raise asyncio.CancelledError
                    raise raise_connection_error()

            transport.write_registers = apply_without_reply
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else DeviceError):
                await current(6)
            transport.write_registers = original
            assert json.loads(service.state_path.read_text())["charging_setpoint"] == 6
            release_flush.set()
            await flush
            assert current_writes(transport) == [8, 6]
        finally:
            release_flush.set()
            await service.stop()

        # A newly initialized service restores the last explicit target, not
        # the superseded slider increase, including through pause and resume.
        restored, new_transport, new_clock = await service_fixture(tmp_path, current=6)
        try:
            assert restored.control.charging_setpoint == 6
            assert not current_writes(new_transport)
            await restored.command({
                "command": "pause", "expected_serial_number": restored.identity.serial_number,
            })
            new_clock[0] += 6
            await restored.command({
                "command": "resume", "expected_serial_number": restored.identity.serial_number,
            })
            assert current_writes(new_transport) == [1, 6]
        finally:
            await restored.stop()

    asyncio.run(run())
