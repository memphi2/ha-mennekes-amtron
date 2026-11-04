"""Automatic recovery must be bounded, current-aware and stopped on unload."""

from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.device.heartbeat import HeartbeatTask
from custom_components.mennekes_amtron_rtu.recovery import HeartbeatRecovery
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.ha_fakes import build_runtime


async def _runtime(*, current=8, error=200, read_only=False, clear_error=True):
    hass, entry, transport = await build_runtime(
        bank=device_bank(error_code=error, charging_current=current),
        read_only=read_only,
    )
    clock = [1000.0]
    runtime = entry.runtime_data
    runtime.client._monotonic = lambda: clock[0]
    runtime.control._monotonic = lambda: clock[0]
    runtime.heartbeat = HeartbeatTask(runtime.client, runtime.diagnostics)
    runtime.heartbeat._set_healthy(True)
    original = transport.write_registers

    async def write(address, values, **kwargs):
        result = await original(address, values, **kwargs)
        if clear_error and address == R.CHARGING_CURRENT_EMS.address:
            put(transport.bank, R.ERROR_CODE, 0)
        return result

    transport.write_registers = write
    recovery = HeartbeatRecovery(hass, entry)
    runtime.recovery = recovery
    return runtime, recovery, transport, clock


async def _finish(recovery):
    task = recovery._task
    assert task is not None
    await asyncio.wait_for(task, 10)


async def _retry(recovery, clock, seconds=31):
    clock[0] += seconds
    assert recovery._retry is not None
    recovery._retry.cancel()
    recovery._retry_due()
    await _finish(recovery)


@pytest.mark.parametrize("current", [0, 1, 6, 8, 16])
def test_recovery_preserves_the_current_and_clears_the_fault(current):
    async def run():
        runtime, recovery, transport, _clock = await _runtime(current=current)
        recovery.start()
        await _finish(recovery)
        assert (
            await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == current
        )
        assert runtime.coordinator.current_data().get("error_code") == 0
        assert runtime.diagnostics.recoveries == 1
        assert recovery._attempts == 0
        assert recovery._retry is None
        assert [address for address, _ in transport.writes] == [
            R.HEARTBEAT.address,
            R.CHARGING_RELEASE.address,
            R.CHARGING_CURRENT_EMS.address,
        ]
        await recovery.async_stop()
        assert not runtime.heartbeat._listeners
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "condition",
    [
        "healthy",
        "different_error",
        "read_only",
        "heartbeat_down",
        "no_heartbeat",
        "poll_failed",
    ],
)
def test_automatic_recovery_only_runs_when_it_is_appropriate(condition):
    async def run():
        runtime, recovery, transport, _clock = await _runtime(
            current=8,
            error=0
            if condition == "healthy"
            else 2011
            if condition == "different_error"
            else 200,
            read_only=condition == "read_only",
        )
        if condition == "heartbeat_down":
            runtime.heartbeat._set_healthy(False)
        elif condition == "no_heartbeat":
            runtime.heartbeat = None
        elif condition == "poll_failed":
            runtime.coordinator.last_update_success = False
        recovery.start()
        assert recovery._task is None
        assert recovery._retry is None
        assert transport.writes == []
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_a_freshly_cleared_fault_receives_no_recovery_writes():
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        recovery.start()
        put(transport.bank, R.ERROR_CODE, 0)
        await _finish(recovery)
        assert transport.writes == []
        assert recovery._retry is None
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_startup_write_floor_is_retried_without_manual_confirmation():
    async def run():
        runtime, recovery, _transport, clock = await _runtime()
        runtime.client.write_state.limiter.record(
            R.CHARGING_CURRENT_EMS.key, now=clock[0]
        )
        recovery.start()
        await _finish(recovery)
        assert runtime.diagnostics.recoveries == 0
        assert recovery._attempts == 0
        await _retry(recovery, clock, 5)
        assert runtime.diagnostics.recoveries == 1
        assert recovery._retry is None
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["persistent_error", "read", "write"])
@pytest.mark.parametrize("current", [1, 8])
def test_recovery_has_a_bounded_retry_budget(failure, current):
    async def run():
        runtime, recovery, transport, clock = await _runtime(
            clear_error=False, current=current
        )
        if failure == "read":
            transport.read_exceptions[R.ERROR_CODE.address] = raise_connection_error()
        elif failure == "write":
            transport.write_exceptions[R.CHARGING_RELEASE.address] = (
                raise_connection_error()
            )
        recovery.start()
        await _finish(recovery)
        for _ in range(2):
            await _retry(recovery, clock)
        assert recovery._attempts == 3
        assert recovery._retry is None
        recovery.async_check()
        assert recovery._task is None
        if current == 1:
            assert all(
                decode_register(R.CHARGING_CURRENT_EMS, words) == 1
                for addr, words in transport.writes
                if addr == R.CHARGING_CURRENT_EMS.address
            )
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("remembered", [None, 0, 8, 16])
def test_recovery_reasserts_unlimited_without_an_intermediate_limit(remembered):
    async def run():
        runtime, recovery, _transport, clock = await _runtime(current=0)
        runtime.control.charging_setpoint = remembered
        slept = []

        async def sleep(delay):
            slept.append(delay)
            clock[0] += delay
            await asyncio.sleep(0)

        runtime.control._sleep = sleep
        recovery.start()
        await _finish(recovery)
        if task := runtime.control._flush_task:
            await task
        assert slept == []
        assert runtime.control._flush_task is None
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == 0
        assert runtime.control.charging_setpoint == 0
        await recovery.async_stop()
        await runtime.control.async_shutdown()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("current", [-1, float("nan"), float("inf")])
def test_invalid_fresh_current_cannot_trigger_recovery(current):
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        recovery.start()
        put(transport.bank, R.CHARGING_CURRENT_EMS, current)
        await _finish(recovery)
        assert runtime.diagnostics.recoveries == 0
        assert recovery._attempts == 1
        assert transport.writes == []
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_a_different_fresh_fault_is_not_reset():
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        recovery.start()
        put(transport.bank, R.ERROR_CODE, 2011)
        await _finish(recovery)
        assert runtime.diagnostics.recoveries == 0
        assert transport.writes == []
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_recovery_has_no_delayed_unlimited_write_that_can_lift_a_later_pause():
    async def run():
        runtime, recovery, transport, clock = await _runtime(current=0)
        recovery.start()
        await _finish(recovery)
        assert runtime.control._flush_task is None
        clock[0] += 5
        await runtime.control.async_pause_charging()
        assert await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS) == 1
        assert runtime.control.charging_setpoint == 0
        assert runtime.control.pause_requested is True
        assert (
            len(
                [
                    addr
                    for addr, _ in transport.writes
                    if addr == R.CHARGING_CURRENT_EMS.address
                ]
            )
            == 2
        )
        await recovery.async_stop()
        await runtime.control.async_shutdown()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("current", [0, 1, 8])
def test_saved_pause_wins_over_changed_current_readback(current):
    async def run():
        runtime, recovery, transport, _clock = await _runtime(current=current)
        runtime.control.pause_requested = True
        runtime.control.charging_setpoint = 10
        recovery.start()
        await _finish(recovery)
        assert runtime.control.charging_setpoint == 10
        assert runtime.control.pause_requested is True
        assert runtime.control.is_paused is True
        assert [
            decode_register(R.CHARGING_CURRENT_EMS, words)
            for addr, words in transport.writes
            if addr == R.CHARGING_CURRENT_EMS.address
        ] == [1]
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_a_device_lock_is_not_removed_or_used_to_skip_recovery():
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        put(transport.bank, R.LOCK_EVSE, 1)
        await runtime.coordinator.async_refresh()
        recovery.start()
        await _finish(recovery)
        assert await runtime.client.async_read_register(R.LOCK_EVSE) == 1
        assert not any(addr == R.LOCK_EVSE.address for addr, _ in transport.writes)
        assert runtime.coordinator.current_data().get("error_code") == 0
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("when", ["before", "queued"])
def test_recovery_reasserts_a_blocked_release_instead_of_enabling_it(when):
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        if when == "queued":
            recovery.start()
            task = recovery._task
        await runtime.control.async_set_charging_release(False)
        if when == "before":
            recovery.start()
            task = recovery._task
        await asyncio.wait_for(task, 10)
        assert [
            values
            for addr, values in transport.writes
            if addr == R.CHARGING_RELEASE.address
        ] == [[0], [0]]
        assert runtime.diagnostics.recoveries == 1
        assert recovery._retry is None
        assert runtime.control.charging_release_requested is False
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_manual_repair_is_not_an_explicit_charging_release():
    async def run():
        runtime, recovery, _transport, _clock = await _runtime()
        await runtime.control.async_set_charging_release(False)
        assert await runtime.control.async_recover_from_error()
        assert runtime.control.charging_release_requested is False
        assert await runtime.client.async_read_register(R.CHARGING_RELEASE) == 0
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


def test_unload_cancels_pending_recovery_and_releases_listeners():
    async def run():
        runtime, recovery, transport, _clock = await _runtime()
        entered = asyncio.Event()
        block = asyncio.Event()
        original = transport.read_holding_registers

        async def read(*args, **kwargs):
            entered.set()
            await block.wait()
            return await original(*args, **kwargs)

        transport.read_holding_registers = read
        recovery.start()
        task = recovery._task
        await asyncio.wait_for(entered.wait(), 10)
        await recovery.async_stop()
        assert task.cancelled()
        assert recovery._retry is None
        assert recovery._remove == []
        assert not runtime.heartbeat._listeners
        assert transport.writes == []
        await recovery.async_stop()
        await runtime.client.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("clear_delay", [0, 2])
def test_recovery_and_repairs_complete_in_real_home_assistant(tmp_path, clear_delay):
    from homeassistant.components.repairs import issue_handler
    from homeassistant.helpers import issue_registry as ir

    from custom_components.mennekes_amtron_rtu.device.const import (
        CONTROL_MODE_MASTER,
        DOMAIN,
    )
    from tests.test_home_assistant_smoke import _boot

    async def run():
        transport = FakeModbusClient(device_bank(error_code=200, charging_current=8))
        original = transport.write_registers
        clear_error = True

        async def write(address, values, **kwargs):
            result = await original(address, values, **kwargs)
            if clear_error and address == R.CHARGING_CURRENT_EMS.address:
                if clear_delay:
                    asyncio.get_running_loop().call_later(
                        clear_delay, put, transport.bank, R.ERROR_CODE, 0
                    )
                else:
                    put(transport.bank, R.ERROR_CODE, 0)
            return result

        transport.write_registers = write
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(tmp_path / "config")
            try:
                hass.data.setdefault("repairs", {})
                issue_handler.async_setup(hass)
                manager = hass.data["repairs"]["flow_manager"]
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
                await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                issues = ir.async_get(hass)
                issue_id = f"{entry.entry_id}_ems_heartbeat_lost"

                async def open_repair():
                    result = await manager.async_init(
                        DOMAIN,
                        context={"issue_id": issue_id},
                        data={"issue_id": issue_id},
                    )
                    assert result["step_id"] == "confirm"
                    return result["flow_id"]

                # A real repairs flow translates the failure instead of raising
                # an unknown error. A stale issue then closes without a write.
                flow_id = await open_repair()
                result = await manager.async_configure(flow_id, {})
                assert result["errors"] == {"base": "read_only"}
                put(transport.bank, R.ERROR_CODE, 0)
                result = await manager.async_configure(flow_id, {})
                assert result["type"] == "create_entry"
                assert issues.async_get_issue(DOMAIN, issue_id) is None
                assert transport.writes == []

                # Master-mode startup recovers an already-active fault without
                # a button press, including the cold-start write floor.
                put(transport.bank, R.ERROR_CODE, 200)
                hass.config_entries.async_update_entry(
                    entry, options={"control_mode": CONTROL_MODE_MASTER}
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data

                async def recovered(count):
                    ready = asyncio.Event()

                    def check():
                        if (
                            runtime.diagnostics.recoveries >= count
                            and issues.async_get_issue(DOMAIN, issue_id) is None
                        ):
                            ready.set()

                    remove = runtime.coordinator.async_add_listener(check)
                    try:
                        check()
                        await asyncio.wait_for(ready.wait(), 12)
                    except TimeoutError:
                        pytest.fail(
                            repr(
                                {
                                    "recoveries": runtime.diagnostics.recoveries,
                                    "task": runtime.recovery._task,
                                    "retry": runtime.recovery._retry,
                                    "attempts": runtime.recovery._attempts,
                                    "heartbeat": runtime.heartbeat.healthy,
                                    "paused": runtime.control.is_paused,
                                    "error": runtime.coordinator.current_data().get(
                                        "error_code"
                                    ),
                                    "lock": runtime.coordinator.current_data().get(
                                        "lock_evse"
                                    ),
                                    "writes": transport.writes,
                                }
                            )
                        )
                    finally:
                        remove()

                await asyncio.wait_for(recovered(1), 15)
                assert (
                    await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS)
                    == 8
                )
                assert (
                    hass.states.get("binary_sensor.mennekes_amtron_heartbeat").state
                    == "off"
                )

                # A new fault fifteen seconds later is its own incident.
                clock = [runtime.control._monotonic() + 15]
                runtime.client._monotonic = lambda: clock[0]
                runtime.control._monotonic = lambda: clock[0]
                put(transport.bank, R.ERROR_CODE, 200)
                await runtime.coordinator.async_refresh()
                await asyncio.wait_for(recovered(2), 10)
                assert (
                    await runtime.client.async_read_register(R.CHARGING_CURRENT_EMS)
                    == 8
                )

                # Manual recovery also stays open until the device confirms
                # success, and then the actual HA manager removes the issue.
                await runtime.recovery.async_stop()
                clear_error = False
                clock[0] += 10
                put(transport.bank, R.ERROR_CODE, 200)
                await runtime.coordinator.async_refresh()
                flow_id = await open_repair()
                result = await manager.async_configure(flow_id, {})
                assert result["errors"] == {"base": "still_active"}
                assert issues.async_get_issue(DOMAIN, issue_id) is not None
                clear_error = True
                clock[0] += 10
                result = await manager.async_configure(flow_id, {})
                assert result["type"] == "create_entry"
                assert issues.async_get_issue(DOMAIN, issue_id) is None
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert runtime.recovery._remove == []
                assert runtime.recovery._retry is None
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))


@pytest.mark.parametrize(
    ("blocked", "reset_current"),
    [("pause", 1), ("pause", 0), ("pause", 8), ("release", 8)],
)
def test_paused_restart_and_manual_repair_never_allow_charging(
    tmp_path, blocked, reset_current
):
    from homeassistant.components.repairs import issue_handler
    from homeassistant.helpers import issue_registry as ir

    from custom_components.mennekes_amtron_rtu.device.const import (
        CONTROL_MODE_MASTER,
        DOMAIN,
    )
    from tests.test_home_assistant_smoke import _boot

    async def run():
        target = tmp_path / "config"
        transport = FakeModbusClient(device_bank(charging_current=8))
        original_write = transport._write
        seen = set()
        permitted = []
        required = {
            R.HEARTBEAT.address,
            R.CHARGING_RELEASE.address,
            R.CHARGING_CURRENT_EMS.address,
        }

        def current():
            return decode_register(
                R.CHARGING_CURRENT_EMS,
                [
                    transport.bank[R.CHARGING_CURRENT_EMS.address + offset]
                    for offset in range(2)
                ],
            )

        def write(address, words, function):
            result = original_write(address, words, function)
            if transport.bank[R.ERROR_CODE.address] == 200:
                seen.add(address)
                # Model reinitialisation on receipt of all three EMS commands.
                # Firmware acceptance of a paused recovery still needs hardware
                # verification; command safety is checked at every transaction.
                if address == R.CHARGING_CURRENT_EMS.address and required <= seen:
                    put(transport.bank, R.ERROR_CODE, 0)
            permitted.append(
                transport.bank[R.ERROR_CODE.address] == 0
                and transport.bank[R.CHARGING_RELEASE.address] == 1
                and transport.bank[R.LOCK_EVSE.address] == 0
                and (current() == 0 or current() >= 6)
            )
            return result

        transport._write = write
        with patch(
            "custom_components.mennekes_amtron_rtu.device.client.AsyncModbusSerialClient",
            return_value=transport,
        ):
            hass = await _boot(target)
            try:
                flow = await hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": "user"}
                )
                flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "serial"})
                await hass.config_entries.flow.async_configure(
                    flow["flow_id"],
                    {
                        "port": "/dev/fake",
                        "device_id": 50,
                        "baudrate": 57600,
                        "frame": "8n2",
                        "search": "off",
                    },
                )
                await hass.async_block_till_done()
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                hass.config_entries.async_update_entry(
                    entry, options={"control_mode": CONTROL_MODE_MASTER}
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                clock = [runtime.control._monotonic() + 6]
                runtime.control._monotonic = runtime.client._monotonic = lambda: clock[
                    0
                ]
                entity_id = "switch.mennekes_amtron_" + (
                    "charging_paused" if blocked == "pause" else "charging_release"
                )
                intent_key = (
                    "pause_requested"
                    if blocked == "pause"
                    else "charging_release_requested"
                )
                await hass.services.async_call(
                    "switch",
                    "turn_on" if blocked == "pause" else "turn_off",
                    {"entity_id": entity_id},
                    blocking=True,
                )
                await hass.async_block_till_done()
                assert getattr(runtime.control, intent_key) is (blocked == "pause")
                assert intent_key not in hass.states.get(entity_id).attributes
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert getattr(entry.runtime_data.control, intent_key) is (
                    blocked == "pause"
                )
            finally:
                await hass.async_stop()

            shutil.rmtree(target / "custom_components" / DOMAIN)
            put(transport.bank, R.ERROR_CODE, 200)
            put(transport.bank, R.CHARGING_CURRENT_EMS, reset_current)
            transport.writes.clear()
            permitted.clear()
            seen.clear()
            hass = await _boot(target)
            try:
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                assert getattr(runtime.control, intent_key) is (blocked == "pause")
                complete = asyncio.Event()

                def check():
                    if runtime.coordinator.current_data().get("error_code") == 0:
                        complete.set()

                remove = runtime.coordinator.async_add_listener(check)
                try:
                    check()
                    await asyncio.wait_for(complete.wait(), 15)
                finally:
                    remove()
                if task := runtime.recovery._task:
                    await asyncio.wait_for(task, 10)
                await hass.async_block_till_done()
                assert runtime.diagnostics.recoveries == 1
                assert not any(permitted)
                assert runtime.recovery._retry is None
                assert hass.states.get(entity_id).state == (
                    "on" if blocked == "pause" else "off"
                )
                if blocked == "pause":
                    assert current() == 1
                    assert runtime.control.charging_setpoint == 8
                    assert all(
                        decode_register(R.CHARGING_CURRENT_EMS, words) == 1
                        for addr, words in transport.writes
                        if addr == R.CHARGING_CURRENT_EMS.address
                    )

                # The same stopped state must survive an explicit repair too.
                await runtime.recovery.async_stop()
                clock = [runtime.control._monotonic() + 30]
                runtime.control._monotonic = runtime.client._monotonic = lambda: clock[
                    0
                ]
                put(transport.bank, R.ERROR_CODE, 200)
                seen.clear()
                await runtime.coordinator.async_refresh()
                issue_id = f"{entry.entry_id}_ems_heartbeat_lost"
                hass.data.setdefault("repairs", {})
                issue_handler.async_setup(hass)
                manager = hass.data["repairs"]["flow_manager"]
                flow = await manager.async_init(
                    DOMAIN, context={"issue_id": issue_id}, data={"issue_id": issue_id}
                )
                result = await manager.async_configure(flow["flow_id"], {})
                assert result["type"] == "create_entry"
                assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
                assert not any(permitted)
                assert getattr(runtime.control, intent_key) is (blocked == "pause")

                # Only an explicit resume/release action permits charging.
                clock[0] += 6
                await hass.services.async_call(
                    "switch",
                    "turn_off" if blocked == "pause" else "turn_on",
                    {"entity_id": entity_id},
                    blocking=True,
                )
                assert permitted[-1] is True
                if blocked == "pause":
                    assert current() == 8
                    assert runtime.control.pause_requested is False
            finally:
                await hass.async_stop()

    asyncio.run(asyncio.wait_for(run(), 60))
