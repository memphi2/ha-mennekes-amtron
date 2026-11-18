"""The service keeps its charge meter and the car's name through its own restart."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from custom_components.mennekes_amtron_rtu.device.charge_meter_store import (
    ChargeMeterStore,
)
from custom_components.mennekes_amtron_rtu.device.enums import EvseState
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.service_api import ServiceApi
from custom_components.mennekes_amtron_rtu.service_client import ServiceClient
from custom_components.mennekes_amtron_rtu.service_transport import EmbeddedTransport
from tests.test_energy_sample import observe, owner

WALL = "custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock"


def note(service, plugged_at, text, **changes):
    return {
        "command": "charge_note", "plugged_at": plugged_at, "note": text,
        "expected_serial_number": service.identity.serial_number, **changes,
    }


def test_a_plug_cycle_and_its_note_survive_a_restart_of_the_service(tmp_path):
    async def run():
        clock = [1000.0]
        with patch(WALL, side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            await observe(service, transport, clock, 1010, EvseState.IDLE, 100)
            await observe(service, transport, clock, 1020, EvseState.CHARGING, 100)
            # Home Assistant names the car through its ordinary command path.
            client = ServiceClient(transport=EmbeddedTransport(ServiceApi(service)))
            client.apply_snapshot(service.snapshot())
            client._connected = True
            await client.execute("charge_note", plugged_at=1020, note="car")
            assert client.snapshot["charge_meter"]["note"] == "car"
            await observe(service, transport, clock, 1030, EvseState.CHARGING, 100)
            await service.stop()

            clock[0] = 1060
            service, transport = await owner(tmp_path, state=EvseState.CHARGING)
            try:
                assert service.charge_meter.note == "car"
                await observe(service, transport, clock, 1070, EvseState.CHARGING, 106)
                await observe(service, transport, clock, 1080, EvseState.IDLE, 106)
                meter = service.snapshot()["charge_meter"]
                assert (meter["last"], meter["last_note"]) == ([1020, 1080], "car")
                assert service.charge_meter.log.energy_between(1020, 1080) == pytest.approx(6)
                # A reconnect of the running service loads nothing a second time.
                service.charge_meter.annotate(1020, "late")
                await service._initialize()
                assert service.charge_meter.last_note == "car"
            finally:
                await service.stop()

    asyncio.run(run())


def test_a_note_needs_the_running_cycle_of_this_wallbox(tmp_path):
    async def run():
        clock = [1000.0]
        with patch(WALL, side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            try:
                await observe(service, transport, clock, 1010, EvseState.IDLE, 100)
                await observe(service, transport, clock, 1020, EvseState.CHARGING, 100)
                for payload in (
                    note(service, 1020, "car", extra=1), note(service, True, "car"),
                    note(service, "1020", "car"), note(service, float("nan"), "car"), note(service, 10**400, "car"),
                    note(service, 1020, ""), note(service, 1020, 5),
                ):
                    with pytest.raises(ValueError, match="note"):
                        await service.command(payload)
                with pytest.raises(DeviceError, match="device_identity_changed"):
                    await service.command(note(service, 1020, "car", expected_serial_number="other"))
                # A cycle that is over keeps what it had; the answer shows it.
                result = await service.command(note(service, 1019, "car"))
                assert result["charge_meter"]["note"] is None
                # While another command runs, its published state is not this one's.
                async with service._command_lock:
                    result = await service.command(note(service, 1020, "car"))
                assert result["charge_meter"]["note"] == "car"
                assert service._published is None
                service._stopping = True
                with pytest.raises(DeviceError, match="service_unavailable"):
                    await service.command(note(service, 1020, "other"))
                service._stopping = False
                assert service.charge_meter.note == "car"
            finally:
                await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("content", [b"{", b'{"version":1,"identity":"other","meter":null}',
                                     b'{"version":1,"identity":"ABC123456789","meter":{}}'])
def test_an_unusable_saved_meter_starts_measuring_again(tmp_path, caplog, content):
    async def run():
        (tmp_path / "charge-meter.json").write_bytes(content)
        service, _transport = await owner(tmp_path)
        try:
            assert service.charge_meter.log.first_at is not None
            assert service.charge_meter.last is None
            assert "Charging loss: saved meter log unusable; starting without it (ValueError)" in caplog.messages
        finally:
            await service.stop()

    caplog.set_level(logging.WARNING)
    asyncio.run(run())


def test_the_meter_is_saved_at_a_change_and_else_every_five_minutes(tmp_path, monkeypatch, caplog):
    async def run():
        clock = [1000.0]
        with patch(WALL, side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            saves, failing = [], []
            save = ChargeMeterStore.save

            def counted(store, value):
                saves.append(value["plugged_at"])
                if failing:
                    raise failing.pop()
                save(store, value)

            monkeypatch.setattr(ChargeMeterStore, "save", counted)
            try:
                await observe(service, transport, clock, 1010, EvseState.IDLE, 100)
                await observe(service, transport, clock, 1020, EvseState.IDLE, 100)
                assert saves == []
                await observe(service, transport, clock, 1030, EvseState.CHARGING, 100)
                await observe(service, transport, clock, 1040, EvseState.CHARGING, 101)
                assert saves == [1030]
                cycle, at = service._meter_saved
                service._meter_saved = (cycle, at - 301)
                await observe(service, transport, clock, 1050, EvseState.CHARGING, 102)
                assert saves == [1030, 1030]
                # A disk that refuses is reported once and costs no measurement.
                # It is tried again after a minute, not five minutes later.
                failing.extend([OSError("full"), OSError("full")])
                service._meter_saved = None
                for at in (1060, 1070, 1080, 1090):
                    await observe(service, transport, clock, at, EvseState.CHARGING, 103)
                    assert service._meter_retry_at > 0 or len(saves) == 5
                    service._meter_retry_at -= 61
                assert len(saves) == 5
                assert [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING] == [
                    "Charging loss: meter log not saved (OSError)",
                ]
                assert "Charging loss: meter log saved again" in caplog.messages
                assert service.charge_meter.plugged_at == 1030
            finally:
                await service.stop()
            assert saves[-1] == 1030

    caplog.set_level(logging.DEBUG)
    asyncio.run(run())
