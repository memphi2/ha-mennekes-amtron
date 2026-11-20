"""The bus core at its edges: a reopened port, a link that changes, a bad meter."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from pymodbus.exceptions import ModbusIOException

from custom_components.mennekes_amtron_rtu.device import client as client_module
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
    create_serial_client,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import AmtronBusError
from custom_components.mennekes_amtron_rtu.device.heartbeat import HeartbeatTask
from tests.fakes import FakeModbusClient, device_bank, put


def test_a_lost_serial_port_is_reopened_within_seconds(monkeypatch):
    seen = {}
    monkeypatch.setattr(client_module, "AsyncModbusSerialClient", lambda *_args, **kwargs: seen.update(kwargs))
    create_serial_client(SerialConfig(port="/dev/null-not-opened"))
    # The library's own default doubles its wait up to five minutes.
    assert seen["reconnect_delay_max"] <= 5.0
    assert seen["retries"] == 0


def test_a_write_does_not_follow_a_link_that_changed_in_the_quiet_wait():
    async def run():
        fake = FakeModbusClient(device_bank(serial="WALLBOX-A"))
        fake.connected = True
        client = MennekesModbusClient(SerialConfig(port="/dev/null-not-opened"))
        client._client = fake
        client.bind_identity("WALLBOX-A")
        real = fake.read_holding_registers
        lost = [True]

        async def read(address, *, count=1, device_id=1):
            if address == R.SERIAL_NUMBER.address and lost and lost.pop():
                # The first reply is lost and the retry answers: the line is suspect.
                raise ModbusIOException("No response received")
            return await real(address, count=count, device_id=device_id)

        fake.read_holding_registers = read
        task = asyncio.create_task(client.async_write_register(R.CHARGING_CURRENT_EMS, 16.0))
        await asyncio.sleep(0.3)
        assert not fake.writes
        assert client.identity_verified
        # The library reopens the port by itself; another device may answer now.
        put(fake.bank, R.SERIAL_NUMBER, "WALLBOX-B")
        client._connection_changed(False)
        client._connection_changed(True)
        with pytest.raises(AmtronBusError):
            await task
        assert not fake.writes

    asyncio.run(run())


def test_a_late_heartbeat_is_reported_once_per_episode(caplog):
    manager = HeartbeatTask.__new__(HeartbeatTask)
    from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
    from custom_components.mennekes_amtron_rtu.device.notice import FailureNotice

    manager._delivered_at = None
    manager._deadline = 10.0
    manager._diagnostics = WriteDiagnostics()
    manager._client = SimpleNamespace(config=SimpleNamespace(port="/dev/ttyUSB0"))
    manager._late_notice = FailureNotice(logging.getLogger(HeartbeatTask.__module__), "Heartbeat")
    manager._late_at = None
    with caplog.at_level(logging.DEBUG):
        # Late and on time in turns is one weak phase; five quiet minutes end it.
        for at in (0.0, 15.0, 20.0, 35.0, 40.0, 60.0, *range(65, 380, 5), 400.0):
            manager._note_delivery(float(at))
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert manager._diagnostics.heartbeats_late == 4
    assert any("within the deadline again" in record.getMessage() for record in caplog.records)


def test_the_owners_connect_ends_the_librarys_own_reopening():
    async def run():
        fake = FakeModbusClient(device_bank())
        reopening = asyncio.create_task(asyncio.sleep(3600))
        fake.ctx = SimpleNamespace(reconnect_task=reopening)
        client = MennekesModbusClient(SerialConfig(port="/dev/null-not-opened"))
        client._client = fake
        await client.async_connect()
        await asyncio.sleep(0)
        # Two openers of one port: the second fails on its lock for good.
        assert reopening.cancelled()
        assert fake.ctx.reconnect_task is None

    asyncio.run(run())


def test_a_weak_phase_lasts_until_five_minutes_without_a_late_heartbeat(caplog):
    from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
    from custom_components.mennekes_amtron_rtu.device.notice import FailureNotice

    manager = HeartbeatTask.__new__(HeartbeatTask)
    manager._delivered_at = None
    manager._deadline = 10.0
    manager._diagnostics = WriteDiagnostics()
    manager._client = SimpleNamespace(config=SimpleNamespace(port="/dev/ttyUSB0"))
    manager._late_notice = FailureNotice(logging.getLogger(HeartbeatTask.__module__), "Heartbeat")
    manager._late_at = None
    with caplog.at_level(logging.DEBUG):
        # Late, late again later, then on time until five minutes after the
        # first late one but not after the second: still the same weak phase.
        for at in (0.0, 15.0, *range(20, 200, 5), 210.0, *range(215, 335, 5)):
            manager._note_delivery(float(at))
    assert len([record for record in caplog.records if record.levelno == logging.WARNING]) == 1
    assert not any("within the deadline again" in record.getMessage() for record in caplog.records)
    assert manager._diagnostics.heartbeats_late == 2
