"""A poll started before a write cannot roll back confirmed control state."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.mennekes_amtron_rtu.data import WallboxData
from custom_components.mennekes_amtron_rtu.device import registers as R
from tests.fakes import device_bank, put, raise_connection_error
from tests.ha_fakes import build_runtime


@pytest.mark.parametrize("outcome", ["success", "lost_reply", "cancelled"])
@pytest.mark.parametrize("command", ["current", "unlimited", "recovery"])
def test_poll_finishing_after_pause_cannot_resume_early(outcome, command):
    async def run():
        _, entry, transport = await build_runtime(bank=device_bank(charging_current=8, error_code=200))
        runtime = entry.runtime_data
        control, client, coordinator = runtime.control, runtime.client, runtime.coordinator
        clock = [1000.0]
        client._monotonic = control._monotonic = lambda: clock[0]
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
        poll = asyncio.create_task(coordinator._async_update_data())
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
                with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else HomeAssistantError):
                    await control.async_pause_charging()
                transport.write_registers = original_write
            clock[0] += 4
            finish_poll.set()
            coordinator.data = await poll
            assert coordinator.data.get("charging_current_ems") == 8
            assert control.is_paused is (True if outcome == "success" else None)
            action = (control.async_recover_from_error() if command == "recovery" else
                      control.async_set_charging_current(0 if command == "unlimited" else 12, allow_unlimited=True))
            with pytest.raises(ServiceValidationError) as limited:
                await action
            assert limited.value.translation_placeholders == {"seconds": "1"}
            assert len([w for w in transport.writes if w[0] == R.CHARGING_CURRENT_EMS.address]) == 1
            # A later poll can still report an actual external state change.
            client.async_read_block = original_read
            put(transport.bank, R.CHARGING_CURRENT_EMS, 9)
            coordinator.data = await coordinator._async_update_data()
            assert control.is_paused is False
        finally:
            finish_poll.set()
            await poll
            await control.async_shutdown()
            await client.async_close()

    asyncio.run(run())


def test_poll_generation_does_not_trigger_entity_notifications():
    assert WallboxData(values={"charging_current_ems": 8}, current_write_generation=1) == WallboxData(
        values={"charging_current_ems": 8}, current_write_generation=2
    )
