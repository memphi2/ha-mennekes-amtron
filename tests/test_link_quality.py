"""Communication quality reflects wire results, not device refusals or traffic."""

from __future__ import annotations

import asyncio

import pytest
from pymodbus.exceptions import ModbusIOException
from pymodbus.pdu import ExceptionResponse

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import MennekesModbusClient
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronBusError,
    AmtronConnectionError,
    AmtronIdentityError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
)
from custom_components.mennekes_amtron_rtu.device.connection import SerialConfig
from custom_components.mennekes_amtron_rtu.device.data import (
    ConnectionState,
    DeviceIdentity,
    WallboxData,
    WriteDiagnostics,
)
from custom_components.mennekes_amtron_rtu.device.heartbeat import HeartbeatTask
from custom_components.mennekes_amtron_rtu.device.link_quality import LinkQuality
from custom_components.mennekes_amtron_rtu.device.poller import DevicePoller
from tests.fakes import FakeModbusClient, device_bank


def test_quality_requires_real_traffic_after_a_full_quiet_period() -> None:
    clock = [0.0]
    quality = LinkQuality(monotonic=lambda: clock[0])
    assert quality.state is None
    quality.observe(AmtronConnectionError("offline"))
    assert quality.state == "degraded"
    quality.observe()
    assert quality.state == "degraded"
    clock[0] = 299
    quality.observe()
    assert quality.state == "degraded"
    clock[0] = 300
    assert quality.state == "degraded"  # Passing time alone proves nothing.
    quality.observe()
    assert quality.state == "good"
    quality.observe(AmtronProtocolError("reply missing"))
    clock[0] = 599
    quality.observe(AmtronProtocolError("reply missing again"))
    clock[0] = 899
    quality.observe()
    assert quality.state == "good"
    assert quality.failures == 3


@pytest.mark.parametrize("error", [
    AmtronIdentityError("another wallbox"),
    AmtronIllegalAddressError("unsupported"),
    AmtronWriteRejectedError("invalid local value"),
    AmtronRateLimitedError("current", 1),
    asyncio.CancelledError(),
    RuntimeError("local failure"),
])
def test_non_communication_errors_do_not_change_quality(error) -> None:
    quality = LinkQuality()
    quality.observe(error)
    assert quality.state is None
    quality.observe()
    quality.observe(error)
    assert quality.state == "good"


def _client(transport, clock, *, retries=0):
    return MennekesModbusClient(
        SerialConfig(port="/dev/fake", retries=retries),
        client_factory=lambda _: transport, monotonic=lambda: clock[0],
    )


@pytest.mark.parametrize("code", [1, 2, 3, 4, 6, 11])
@pytest.mark.parametrize("write", [False, True])
def test_valid_negative_responses_confirm_communication(code, write) -> None:
    async def run():
        transport = FakeModbusClient(device_bank())
        client = _client(transport, [0.0])
        await client.async_connect()
        if write:
            transport.write_exceptions[R.HEARTBEAT.address] = code
            operation = client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
        else:
            transport.read_exceptions[R.EVSE_STATE.address] = code
            operation = client.async_read_register(R.EVSE_STATE)
        with pytest.raises(AmtronBusError):
            await operation
        assert client.link_quality.state == "good"
        await client.async_close()

    asyncio.run(run())


def test_a_successful_read_retry_counts_as_a_retry_not_a_failure() -> None:
    async def run():
        class RetryTransport(FakeModbusClient):
            failed_once = False

            async def read_holding_registers(self, address, *, count, device_id):
                if not self.failed_once:
                    self.failed_once = True
                    raise ModbusIOException("private timeout detail")
                return await super().read_holding_registers(address, count=count, device_id=device_id)

        clock = [0.0]
        transport = RetryTransport(device_bank())
        client = _client(transport, clock, retries=1)
        await client.async_connect()
        assert await client.async_read_register(R.EVSE_STATE) == 1
        assert client.link_quality.state == "good"
        assert (client.link_quality.retries, client.link_quality.failures) == (1, 0)
        await client.async_close()

    asyncio.run(run())


def test_mismatched_exception_response_is_a_communication_error() -> None:
    async def run():
        class WrongFunction(FakeModbusClient):
            async def read_holding_registers(self, address, *, count, device_id):
                return ExceptionResponse(0x06, 4)

        client = _client(WrongFunction(device_bank()), [0.0])
        await client.async_connect()
        with pytest.raises(AmtronProtocolError, match="another function"):
            await client.async_read_register(R.EVSE_STATE)
        assert client.link_quality.state == "degraded"
        await client.async_close()

    asyncio.run(run())


def test_heartbeat_failure_latches_until_a_fresh_success_after_the_window() -> None:
    async def run():
        clock = [0.0]
        transport = FakeModbusClient(device_bank())
        client = _client(transport, clock)
        await client.async_connect()
        poller = DevicePoller(
            client, DeviceIdentity(layout_version=R.LAYOUT_V01_03),
            connection_state=ConnectionState(), monotonic=lambda: clock[0],
        )
        first = await poller.async_poll(WallboxData(), last_success=True)
        assert first.connection_quality == "good"
        assert first.connection_failures == 0
        # A lost heartbeat reply is resent within a second: a retry.
        transport.write_exceptions[R.HEARTBEAT.address] = ModbusIOException("lost reply")
        with pytest.raises(AmtronProtocolError, match="lost reply"):
            await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
        assert client.link_quality.state == "good"
        retried = await poller.async_poll(first, last_success=True)
        assert (retried.connection_retries, retried.connection_failures) == (1, 0)
        assert retried != first  # A new count must notify entities.
        # Any other lost reply is a failure.
        transport.write_exceptions[R.CHARGING_RELEASE.address] = ModbusIOException("lost reply")
        with pytest.raises(AmtronProtocolError, match="lost reply"):
            await client.async_write_register(R.CHARGING_RELEASE, 1)
        del transport.write_exceptions[R.CHARGING_RELEASE.address]
        assert client.link_quality.state == "degraded"
        later = await poller.async_poll(retried, last_success=True)
        assert later.connection_quality == "degraded"
        assert later.connection_failures == 1
        assert first.values == later.values
        assert first != later  # A quality transition must notify entities.
        del transport.write_exceptions[R.HEARTBEAT.address]
        clock[0] = 299
        await client.async_write_register(R.HEARTBEAT, 0x55AA, priority=True)
        assert client.link_quality.state == "degraded"
        clock[0] = 300
        healthy = await poller.async_poll(later, last_success=True)
        assert healthy.connection_quality == "good"
        same = await poller.async_poll(healthy, last_success=True)
        assert same == healthy  # More successful traffic does not create rows.
        await client.async_close()

    asyncio.run(run())


def test_energy_read_marks_fresh_and_failed_attempts_without_changing_snapshot_equality() -> None:
    async def run():
        clock = [0.0]
        transport = FakeModbusClient(device_bank())
        client = _client(transport, clock)
        await client.async_connect()
        poller = DevicePoller(
            client, DeviceIdentity(layout_version=R.LAYOUT_V01_03),
            connection_state=ConnectionState(), monotonic=lambda: clock[0],
        )
        first = await poller.async_poll(WallboxData(), last_success=True)
        assert first.energy_read
        cached = await poller.async_poll(first, last_success=True)
        assert not cached.energy_read
        assert first == cached
        clock[0] = 60
        transport.read_exceptions[0x1000] = 4
        failed = await poller.async_poll(cached, last_success=True)
        assert failed.energy_read
        assert failed.get("energy_total") is None
        assert failed.connection_quality == "good"  # Device error, not a bad link.
        assert failed.connection_failures == 0
        await client.async_close()

    asyncio.run(run())


def test_poll_keeps_goal_generation_from_before_its_first_read() -> None:
    async def run():
        entered, resume = asyncio.Event(), asyncio.Event()

        class PausedTransport(FakeModbusClient):
            paused_once = False

            async def read_holding_registers(self, address, *, count, device_id):
                if not self.paused_once:
                    self.paused_once = True
                    entered.set()
                    await resume.wait()
                return await super().read_holding_registers(address, count=count, device_id=device_id)

        client = _client(PausedTransport(device_bank()), [0.0])
        await client.async_connect()
        poller = DevicePoller(
            client, DeviceIdentity(layout_version=R.LAYOUT_V01_03),
            connection_state=ConnectionState(),
        )
        pending = asyncio.create_task(poller.async_poll(WallboxData(), last_success=True))
        await asyncio.wait_for(entered.wait(), 1)
        client.write_state.energy_goal_generation += 1
        resume.set()
        old = await asyncio.wait_for(pending, 1)
        assert old.energy_goal_generation == 0
        assert client.write_state.energy_goal_generation == 1
        new = await poller.async_poll(old, last_success=True)
        assert new.energy_goal_generation == 1
        assert old == new  # Internal ordering must not create Recorder updates.
        await client.async_close()

    asyncio.run(run())


def test_a_missed_heartbeat_deadline_is_a_failure() -> None:
    async def run():
        client = _client(FakeModbusClient(device_bank()), [0.0])
        heartbeat = HeartbeatTask(client, WriteDiagnostics(), deadline=0.01)
        heartbeat._arm_deadline()
        await asyncio.sleep(0.05)
        assert client.link_quality.failures == 1
        assert heartbeat.healthy is False

    asyncio.run(run())
