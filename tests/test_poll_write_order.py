"""A poll started before a write cannot roll back confirmed control state."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    DeviceValidationError,
)
from tests.fakes import put, raise_connection_error
from tests.test_modbus_service import service_fixture


@pytest.mark.parametrize("outcome", ["success", "lost_reply", "cancelled"])
@pytest.mark.parametrize("command", ["current", "unlimited", "recovery"])
def test_poll_finishing_after_pause_cannot_resume_early(tmp_path, outcome, command):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        put(transport.bank, R.ERROR_CODE, 200)
        await service.refresh()
        transport.writes.clear()
        control, client = service.control, service.client
        # The service publishes after every command by polling again. That
        # poll would queue behind the delayed one; only the stale poll counts.
        control._request_refresh = None
        current_read = asyncio.Event()
        finish_poll = asyncio.Event()
        original_read = client.async_read_block

        async def delayed_read(block):
            values = await original_read(block)
            if block.name == "current_limits":
                current_read.set()
                await finish_poll.wait()
            return values

        client.async_read_block = delayed_read
        poll = asyncio.create_task(service.refresh())
        try:
            await current_read.wait()
            if outcome == "success":
                await control.async_pause_charging()
            else:
                original_write = transport.write_registers

                async def applied_without_reply(*args, **kwargs):
                    await original_write(*args, **kwargs)
                    if outcome == "cancelled":
                        raise asyncio.CancelledError
                    raise raise_connection_error()

                transport.write_registers = applied_without_reply
                with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else DeviceError):
                    await control.async_pause_charging()
                transport.write_registers = original_write
            clock[0] += 4
            finish_poll.set()
            await poll
            assert service.data.get("charging_current_ems") == 8
            assert control.is_paused is (True if outcome == "success" else None)
            action = (control.async_recover_from_error() if command == "recovery" else
                      control.async_set_charging_current(0 if command == "unlimited" else 12, allow_unlimited=True))
            if command == "recovery":
                with pytest.raises(DeviceValidationError) as limited:
                    await action
                assert limited.value.translation_placeholders == {"seconds": "1"}
            else:
                await action
                assert control.pending_action == "set_current"
            assert len([w for w in transport.writes if w[0] == R.CHARGING_CURRENT_EMS.address]) == 1
            # A later poll can still report an actual external state change.
            client.async_read_block = original_read
            put(transport.bank, R.CHARGING_CURRENT_EMS, 9)
            await service.refresh()
            assert control.is_paused is False
        finally:
            finish_poll.set()
            await poll
            await service.stop()

    asyncio.run(run())


def test_poll_generation_does_not_trigger_entity_notifications():
    assert WallboxData(values={"charging_current_ems": 8}, current_write_generation=1) == WallboxData(
        values={"charging_current_ems": 8}, current_write_generation=2
    )
