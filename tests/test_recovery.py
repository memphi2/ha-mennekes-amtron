"""Automatic recovery must be bounded, current-aware and stopped with the service."""

from __future__ import annotations

import asyncio
import shutil
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device import service_runtime
from custom_components.mennekes_amtron_rtu.device.decode import decode_register
from custom_components.mennekes_amtron_rtu.sensor import SENSORS
from tests.fakes import FakeModbusClient, device_bank, put, raise_connection_error
from tests.test_modbus_service import service_fixture


async def _runtime(monkeypatch, tmp_path, *, current=8, error=200, read_only=False, clear_error=True):
    """A service whose wallbox reports ``error`` and clears it on a current write."""
    service, transport, clock = await service_fixture(tmp_path, read_only=read_only, current=current)
    # The service's retry window runs on the same simulated clock as its bus.
    monkeypatch.setattr(service_runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    await service.heartbeat.async_stop()
    put(transport.bank, R.ERROR_CODE, error)
    await service.refresh()
    service.heartbeat._set_healthy(True)
    original = transport.write_registers

    async def write(address, values, **kwargs):
        result = await original(address, values, **kwargs)
        if clear_error and address == R.CHARGING_CURRENT_EMS.address:
            put(transport.bank, R.ERROR_CODE, 0)
        return result

    transport.write_registers = write
    transport.writes.clear()
    return service, transport, clock


def _current_writes(transport):
    return [
        decode_register(R.CHARGING_CURRENT_EMS, words)
        for address, words in transport.writes
        if address == R.CHARGING_CURRENT_EMS.address
    ]


async def _one_poll_cycle(service):
    """Run the service's own loop exactly once: poll, then maybe recover."""
    service._attempted.clear()
    task = asyncio.create_task(service._polling_loop())
    try:
        await asyncio.wait_for(service._attempted.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("current", [0, 1, 6, 8, 16])
def test_recovery_preserves_the_current_and_clears_the_fault(monkeypatch, tmp_path, current):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path, current=current)
        try:
            await service._recover()
            assert await service.client.async_read_register(R.CHARGING_CURRENT_EMS) == current
            assert service.data.get("error_code") == 0
            assert service.diagnostics.recoveries == 1
            assert [address for address, _ in transport.writes] == [
                R.HEARTBEAT.address,
                R.CHARGING_RELEASE.address,
                R.CHARGING_CURRENT_EMS.address,
            ]
            # A cleared fault resets the budget and writes nothing more.
            await service._recover()
            assert service._attempts == 0
            assert len(transport.writes) == 3
        finally:
            await service.stop()

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
def test_automatic_recovery_only_runs_when_it_is_appropriate(monkeypatch, tmp_path, condition):
    async def run():
        service, transport, _clock = await _runtime(
            monkeypatch, tmp_path,
            current=8,
            error=0
            if condition == "healthy"
            else 2011
            if condition == "different_error"
            else 200,
            read_only=condition == "read_only",
        )
        try:
            if condition == "heartbeat_down":
                service.heartbeat._set_healthy(False)
            elif condition == "no_heartbeat":
                service.heartbeat._set_healthy(None)
            if condition == "poll_failed":
                transport.read_exceptions[R.ERROR_CODE.address] = raise_connection_error()
                await _one_poll_cycle(service)
                assert service._success is False
            else:
                await service._recover()
            assert service._attempts == 0
            assert service.diagnostics.recoveries == 0
            assert transport.writes == []
        finally:
            await service.stop()

    asyncio.run(run())


def test_a_freshly_cleared_fault_receives_no_recovery_writes(monkeypatch, tmp_path):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        try:
            put(transport.bank, R.ERROR_CODE, 0)
            await service._recover()
            assert transport.writes == []
            assert service._attempts == 0
            assert service.data.get("error_code") == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_fault_returning_during_noop_recovery_retains_a_retry(monkeypatch, tmp_path):
    async def run():
        service, transport, clock = await _runtime(monkeypatch, tmp_path)
        original = transport.read_holding_registers
        first_error_read = True

        async def read(address, **kwargs):
            nonlocal first_error_read
            if address == R.ERROR_CODE.address and first_error_read:
                first_error_read = False
                put(transport.bank, R.ERROR_CODE, 0)
                response = await original(address, **kwargs)
                put(transport.bank, R.ERROR_CODE, 200)
                return response
            return await original(address, **kwargs)

        transport.read_holding_registers = read
        try:
            await service._recover()
            assert not transport.writes
            assert service.data.get("error_code") == 200
            assert service._attempts == 0
            # The retry waits for its window, then recovers the returned fault.
            clock[0] += 29
            await service._recover()
            assert not transport.writes
            clock[0] += 2
            await service._recover()
            assert service.diagnostics.recoveries == 1
            assert service.data.get("error_code") == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_startup_write_floor_is_retried_without_manual_confirmation(monkeypatch, tmp_path):
    async def run():
        service, _transport, clock = await _runtime(monkeypatch, tmp_path)
        try:
            service.client.write_state.limiter.record(R.CHARGING_CURRENT_EMS.key, now=clock[0])
            await service._recover()
            assert service.diagnostics.recoveries == 0
            assert service._attempts == 0
            clock[0] += 5
            await service._recover()
            assert service.diagnostics.recoveries == 1
            assert service.data.get("error_code") == 0
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["persistent_error", "read", "write"])
@pytest.mark.parametrize("current", [1, 8])
def test_recovery_has_a_bounded_retry_budget(monkeypatch, tmp_path, failure, current):
    async def run():
        service, transport, clock = await _runtime(
            monkeypatch, tmp_path, clear_error=False, current=current
        )
        try:
            if failure == "read":
                transport.read_exceptions[R.ERROR_CODE.address] = raise_connection_error()
            elif failure == "write":
                transport.write_exceptions[R.CHARGING_RELEASE.address] = raise_connection_error()
            for _ in range(3):
                await service._recover()
                clock[0] += 31
            assert service._attempts == 3
            writes = list(transport.writes)
            await service._recover()
            assert transport.writes == writes
            if current == 1:
                assert all(value == 1 for value in _current_writes(transport))
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("remembered", [None, 0, 8, 16])
def test_recovery_reasserts_unlimited_without_an_intermediate_limit(monkeypatch, tmp_path, remembered):
    async def run():
        service, _transport, clock = await _runtime(monkeypatch, tmp_path, current=0)
        control = service.control
        control.charging_setpoint = remembered
        slept = []

        async def sleep(delay):
            slept.append(delay)
            clock[0] += delay
            await asyncio.sleep(0)

        control._sleep = sleep
        try:
            await service._recover()
            if task := control._flush_task:
                await task
            assert slept == []
            assert control._flush_task is None
            assert await service.client.async_read_register(R.CHARGING_CURRENT_EMS) == 0
            assert control.charging_setpoint == 0
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("current", [-1, float("nan"), float("inf")])
def test_invalid_fresh_current_cannot_trigger_recovery(monkeypatch, tmp_path, current):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        try:
            put(transport.bank, R.CHARGING_CURRENT_EMS, current)
            await service._recover()
            assert service.diagnostics.recoveries == 0
            assert service._attempts == 1
            assert transport.writes == []
        finally:
            await service.stop()

    asyncio.run(run())


def test_a_different_fresh_fault_is_not_reset(monkeypatch, tmp_path):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        try:
            put(transport.bank, R.ERROR_CODE, 2011)
            await service._recover()
            assert service.diagnostics.recoveries == 0
            assert transport.writes == []
        finally:
            await service.stop()

    asyncio.run(run())


def test_recovery_has_no_delayed_unlimited_write_that_can_lift_a_later_pause(monkeypatch, tmp_path):
    async def run():
        service, transport, clock = await _runtime(monkeypatch, tmp_path, current=0)
        control = service.control
        try:
            await service._recover()
            assert control._flush_task is None
            clock[0] += 5
            await control.async_pause_charging()
            assert await service.client.async_read_register(R.CHARGING_CURRENT_EMS) == 1
            assert control.charging_setpoint == 0
            assert control.pause_requested is True
            assert len(_current_writes(transport)) == 2
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("current", [0, 1, 8])
def test_saved_pause_wins_over_changed_current_readback(monkeypatch, tmp_path, current):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path, current=current)
        control = service.control
        control.pause_requested = True
        control.charging_setpoint = 10
        try:
            await service._recover()
            assert control.charging_setpoint == 10
            assert control.pause_requested is True
            assert control.is_paused is True
            assert _current_writes(transport) == [1]
        finally:
            await service.stop()

    asyncio.run(run())


def test_a_device_lock_is_not_removed_or_used_to_skip_recovery(monkeypatch, tmp_path):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        try:
            put(transport.bank, R.LOCK_EVSE, 1)
            await service.refresh()
            await service._recover()
            assert await service.client.async_read_register(R.LOCK_EVSE) == 1
            assert not any(addr == R.LOCK_EVSE.address for addr, _ in transport.writes)
            assert service.data.get("error_code") == 0
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("when", ["before", "queued"])
def test_recovery_reasserts_a_blocked_release_instead_of_enabling_it(monkeypatch, tmp_path, when):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        control = service.control
        try:
            if when == "queued":
                # Created, not yet run: the block arrives while it waits.
                task = asyncio.create_task(service._recover())
            await control.async_set_charging_release(False)
            if when == "before":
                task = asyncio.create_task(service._recover())
            await asyncio.wait_for(task, 10)
            assert [
                values
                for addr, values in transport.writes
                if addr == R.CHARGING_RELEASE.address
            ] == [[0], [0]]
            assert service.diagnostics.recoveries == 1
            assert control.charging_release_requested is False
        finally:
            await service.stop()

    asyncio.run(run())


def test_manual_repair_is_not_an_explicit_charging_release(monkeypatch, tmp_path):
    async def run():
        service, _transport, _clock = await _runtime(monkeypatch, tmp_path)
        control = service.control
        try:
            await control.async_set_charging_release(False)
            assert await control.async_recover_from_error()
            assert control.charging_release_requested is False
            assert await service.client.async_read_register(R.CHARGING_RELEASE) == 0
        finally:
            await service.stop()

    asyncio.run(run())


def test_stopping_the_service_cancels_a_pending_recovery(monkeypatch, tmp_path):
    async def run():
        service, transport, _clock = await _runtime(monkeypatch, tmp_path)
        entered = asyncio.Event()
        block = asyncio.Event()
        original = transport.read_holding_registers

        async def read(*args, **kwargs):
            entered.set()
            await block.wait()
            return await original(*args, **kwargs)

        transport.read_holding_registers = read
        service._task = asyncio.create_task(service._polling_loop())
        task = service._task
        await asyncio.wait_for(entered.wait(), 10)
        await service.stop()
        assert task.cancelled()
        assert service.diagnostics.recoveries == 0
        assert transport.writes == []

    asyncio.run(run())


async def _stop_home_assistant(hass):
    """Stop HA as a restart does: the process ends, and the service with it."""
    from custom_components.mennekes_amtron_rtu.device.const import DOMAIN

    await hass.async_stop()
    for host in list(hass.data.get(f"{DOMAIN}_embedded", {}).values()):
        await host.async_stop()


@pytest.mark.parametrize("clear_delay", [0, 2])
def test_recovery_and_repairs_complete_in_real_home_assistant(monkeypatch, tmp_path, clear_delay):
    from homeassistant.components.repairs import issue_handler
    from homeassistant.helpers import issue_registry as ir

    from custom_components.mennekes_amtron_rtu.const import (
        CONTROL_MODE_MASTER,
    )
    from custom_components.mennekes_amtron_rtu.device.const import (
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
                    entry, options={"control_mode": CONTROL_MODE_MASTER, "scan_interval_seconds": 2}
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service

                async def recovered(count):
                    ready = asyncio.Event()

                    def check():
                        if (
                            service.diagnostics.recoveries >= count
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
                                    "recoveries": service.diagnostics.recoveries,
                                    "attempts": service._attempts,
                                    "heartbeat": service.heartbeat.healthy,
                                    "paused": service.control.is_paused,
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
                    await service.client.async_read_register(R.CHARGING_CURRENT_EMS)
                    == 8
                )
                # The connection-quality sensor is off by default; its row reads
                # the same runtime that a user enabling it would see.
                quality = next(row for row in SENSORS if row.key == "connection_quality")
                assert quality.attributes(runtime)["heartbeat"] == "ok"

                # A new fault after the service's retry window is its own
                # incident; the service sees that time pass on its own clocks.
                offset = [31.0]
                clock = [service.control._monotonic() + offset[0]]
                service.client._monotonic = lambda: clock[0]
                service.control._monotonic = lambda: clock[0]
                monotonic = time.monotonic
                monkeypatch.setattr(
                    service_runtime, "time", SimpleNamespace(monotonic=lambda: monotonic() + offset[0])
                )
                put(transport.bank, R.ERROR_CODE, 200)
                await service.refresh()
                await runtime.coordinator.async_refresh()
                await asyncio.wait_for(recovered(2), 10)
                assert (
                    await service.client.async_read_register(R.CHARGING_CURRENT_EMS)
                    == 8
                )

                # Manual recovery also stays open until the device confirms
                # success, and then the actual HA manager removes the issue.
                # The automatic recovery is held back so only the repair acts.
                service._retry_at = float("inf")
                clear_error = False
                clock[0] += 10
                put(transport.bank, R.ERROR_CODE, 200)
                await service.refresh()
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
                assert entry.entry_id not in hass.data[f"{DOMAIN}_embedded"]
            finally:
                await _stop_home_assistant(hass)

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

    from custom_components.mennekes_amtron_rtu.const import (
        CONTROL_MODE_MASTER,
    )
    from custom_components.mennekes_amtron_rtu.device.const import (
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
                    entry, options={"control_mode": CONTROL_MODE_MASTER, "scan_interval_seconds": 2}
                )
                await hass.async_block_till_done()
                runtime = entry.runtime_data
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service
                # First accept the deliberate stop after the cold-start
                # transition guard; only executed intent is persisted.
                clock = [service.control._monotonic() + 301]
                service.control._monotonic = service.client._monotonic = lambda: clock[
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
                await _stop_home_assistant(hass)

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
                service = hass.data[f"{DOMAIN}_embedded"][entry.entry_id]._service
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
                await hass.async_block_till_done()
                assert service.diagnostics.recoveries == 1
                assert not any(permitted)
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
                # The automatic recovery is held back so only the repair acts.
                service._retry_at = float("inf")
                clock = [service.control._monotonic() + 30]
                service.control._monotonic = service.client._monotonic = lambda: clock[
                    0
                ]
                put(transport.bank, R.ERROR_CODE, 200)
                seen.clear()
                await service.refresh()
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
                clock[0] += 300
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
                await _stop_home_assistant(hass)

    asyncio.run(asyncio.wait_for(run(), 60))
