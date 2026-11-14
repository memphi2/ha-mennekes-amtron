"""Completed charging metadata must stay independent of control and bus traffic."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.charging_session import SessionHistory
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    SerialConfig,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.energy_goal import EnergyGoal
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.service_config import ServerConfig
from custom_components.mennekes_amtron_rtu.device.service_runtime import ModbusService
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.fakes import FakeModbusClient, device_bank, put
from tests.test_modbus_service import TOKEN


async def owner(tmp_path, *, layout=R.LAYOUT_V01_03, state=EvseState.IDLE, read_only=True):
    bank = device_bank(layout=layout, evse_state=state)
    put(bank, R.CP_STATE, CpState.A1 if state is EvseState.IDLE else CpState.C2)
    put(bank, R.ENERGY_TOTAL, 100)
    transport = FakeModbusClient(bank)
    client = MennekesModbusClient(SerialConfig("/dev/fake"), client_factory=lambda _: transport)
    service = ModbusService(
        ServerConfig(client.config, TOKEN, read_only=read_only), tmp_path / "intent.json", client=client,
    )
    await service._initialize()
    return service, transport


async def observe(service, transport, clock, at, state, energy):
    clock[0] = at
    put(transport.bank, R.EVSE_STATE, state)
    put(transport.bank, R.CP_STATE, CpState.A1 if state is EvseState.IDLE else CpState.C2)
    put(transport.bank, R.ENERGY_TOTAL, energy)
    await service.refresh()


async def complete(service, transport, clock):
    await observe(service, transport, clock, 1005, EvseState.EV_CONNECTED, 100)
    await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
    await observe(service, transport, clock, 1100, EvseState.EV_CONNECTED, 112)
    await observe(service, transport, clock, 1120, EvseState.IDLE, 112)


def test_service_publishes_and_persists_one_complete_session_without_extra_reads(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            try:
                original = await asyncio.to_thread(service.state_path.read_bytes)
                await observe(service, transport, clock, 1005, EvseState.EV_CONNECTED, 100)
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
                assert await asyncio.to_thread(service.state_path.read_bytes) == original
                assert service.snapshot()["charging_session"] == {"supported": True, "latest": None}
                await observe(service, transport, clock, 1100, EvseState.EV_CONNECTED, 112)
                assert service.client.write_state.session_history.latest is None
                # A pause is not the end of a connected charging session.
                assert await asyncio.to_thread(service.state_path.read_bytes) == original
                transport.reads.clear()
                await observe(service, transport, clock, 1120, EvseState.IDLE, 112)
                assert transport.reads.count((R.ENERGY_TOTAL.address, 4)) == 1
                summary = service.snapshot()["charging_session"]["latest"]
                assert summary["wallbox_kwh"] == 12
                assert summary["started_after"] == 1005
                assert summary["started_before"] == 1010
                assert summary["ended_after"] == 1010
                assert summary["ended_before"] == 1100
                assert summary["disconnected_at"] == 1120
                assert service.data.charging_session == summary
                assert service.data.session_summary_supported
                reads = list(transport.reads)
                for _ in range(10):
                    assert service.snapshot()["charging_session"]["latest"] == summary
                assert transport.reads == reads
                assert not transport.writes
                stored = json.loads(await asyncio.to_thread(service.state_path.read_text))
                assert stored["charging_session_history"]["latest"] == summary
                before = await asyncio.to_thread(service.state_path.read_bytes)
                await observe(service, transport, clock, 1130, EvseState.IDLE, 112)
                assert await asyncio.to_thread(service.state_path.read_bytes) == before
                restored = WriteState()
                await asyncio.to_thread(IntentStore(service.state_path, service.identity.serial_number).load, restored)
                assert restored.session_history.latest.as_dict() == summary
            finally:
                await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("interruption", ["cold_start", "poll_failure", "meter_failure"])
def test_incomplete_observation_never_becomes_a_complete_session(tmp_path, interruption):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(
                tmp_path, state=EvseState.CHARGING if interruption == "cold_start" else EvseState.IDLE,
            )
            try:
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
                if interruption == "poll_failure":
                    real_poll = service._poller.async_poll
                    service._poller.async_poll = AsyncMock(side_effect=AmtronConnectionError("offline"))
                    with pytest.raises(AmtronConnectionError, match="offline"):
                        await service.refresh()
                    service._poller.async_poll = real_poll
                elif interruption == "meter_failure":
                    transport.read_exceptions[R.ENERGY_TOTAL.address] = 4
                await observe(service, transport, clock, 1110, EvseState.IDLE, 112)
                assert service.snapshot()["charging_session"] == {"supported": True, "latest": None}
                assert not transport.writes
            finally:
                await service.stop()

    asyncio.run(run())


def test_older_layout_does_not_advertise_unmeasured_sessions(tmp_path):
    async def run():
        service, transport = await owner(tmp_path, layout=R.LAYOUT_V01_00)
        try:
            assert service.snapshot()["charging_session"] == {"supported": False, "latest": None}
            assert not service.data.session_summary_supported
            assert service.data.charging_session is None
            assert not any(address == R.ENERGY_TOTAL.address for address, _ in transport.reads)
        finally:
            await service.stop()

    asyncio.run(run())


def test_session_storage_failure_does_not_make_wallbox_unavailable_and_retries_bounded(tmp_path, caplog):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            real_save = service._store.save
            attempts = []

            def fail_new_history(state):
                attempts.append(state.session_history.snapshot())
                if state.session_history.latest is not None:
                    raise OSError("disk full")
                real_save(state)

            service._store.save = fail_new_history
            try:
                await complete(service, transport, clock)
                assert service.snapshot()["available"]
                assert service._session_history_dirty
                assert service.snapshot()["charging_session"]["latest"]["wallbox_kwh"] == 12
                assert len(attempts) == 1
                # The ordinary control save retains durable history; it must
                # not turn this optional failure into a failed polling loop.
                await service._persist()
                assert attempts[-1]["latest"] is None
                await observe(service, transport, clock, 1130, EvseState.IDLE, 112)
                await observe(service, transport, clock, 1140, EvseState.IDLE, 112)
                assert len(attempts) == 2
                service._session_history_retry_at = 0
                await service.refresh()
                assert len(attempts) == 3
                assert caplog.text.count("Cannot save completed charging session") == 1
                service._store.save = real_save
                service._session_history_retry_at = 0
                await service.refresh()
                assert not service._session_history_dirty
                assert not service._session_history_error_logged
                saved = json.loads(await asyncio.to_thread(service.state_path.read_text))
                assert saved["charging_session_history"]["latest"]["wallbox_kwh"] == 12
                assert service.snapshot()["available"]
            finally:
                service._store.save = real_save
                await service.stop()

    asyncio.run(run())


def test_uncertain_session_save_survives_later_control_save(tmp_path, monkeypatch):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            sync = os.fsync
            try:
                await observe(service, transport, clock, 1005, EvseState.EV_CONNECTED, 100)
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
                await observe(service, transport, clock, 1100, EvseState.EV_CONNECTED, 112)

                def fail_directory(descriptor):
                    if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                        raise OSError("directory sync failed")
                    return sync(descriptor)

                with monkeypatch.context() as patcher:
                    patcher.setattr(os, "fsync", fail_directory)
                    await observe(service, transport, clock, 1120, EvseState.IDLE, 112)
                summary = service.snapshot()["charging_session"]["latest"]
                assert service._saved_session_history["latest"] == summary
                assert service._session_history_dirty
                await service._persist()
                stored = json.loads(await asyncio.to_thread(service.state_path.read_text))
                assert stored["charging_session_history"]["latest"] == summary
                service._session_history_retry_at = 0
                await service._persist_session_history()
                assert not service._session_history_dirty
            finally:
                await service.stop()

    asyncio.run(run())


def test_cold_owner_keeps_last_summary_but_cannot_finish_an_old_active_cycle(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            await complete(service, transport, clock)
            expected = service.snapshot()["charging_session"]["latest"]
            await observe(service, transport, clock, 1200, EvseState.CHARGING, 112)
            await service.stop()
            replacement = ModbusService(service.config, service.state_path, client=service.client)
            try:
                await replacement._initialize()
                assert replacement.snapshot()["charging_session"]["latest"] == expected
                await observe(replacement, transport, clock, 1300, EvseState.IDLE, 114)
                assert replacement.snapshot()["charging_session"]["latest"] is None
                saved = json.loads(await asyncio.to_thread(replacement.state_path.read_text))
                assert saved["charging_session_history"]["latest"] is None
            finally:
                await replacement.stop()

    asyncio.run(run())


@pytest.mark.parametrize("history", [
    None, [], "private-corrupt-history", {"version": 2, "latest": None},
    {"version": 1, "latest": {"wallbox_kwh": "private-corrupt-history"}},
])
def test_store_discards_only_invalid_optional_history(tmp_path, caplog, history):
    path = tmp_path / "intent.json"
    energy_goal = EnergyGoal()
    energy_goal.set_target(12, 100)
    soc_goal = SocGoal()
    soc_goal.configure_source("vehicle-source")
    soc_goal.set_target(80, 50, 90)
    payload = {
        "version": 1, "identity": "wallbox", "pause_requested": True,
        "charging_release_requested": False, "charging_setpoint": 8,
        "energy_goal": energy_goal.snapshot(), "soc_goal": soc_goal.snapshot(),
        "charging_session_history": history,
        "plug_charge": WriteState().plug_charge.durable(),
    }
    path.write_text(json.dumps(payload))
    state = WriteState(pause_requested=False, charging_setpoint=6)
    store = IntentStore(path, "wallbox")
    store.load(state)
    assert state.pause_requested is True
    assert state.charging_release_requested is False
    assert state.charging_setpoint == 8
    assert state.energy_goal.snapshot() == energy_goal.snapshot()
    assert state.soc_goal.snapshot() == soc_goal.snapshot()
    assert state.session_history.snapshot() == SessionHistory().snapshot()
    assert caplog.messages == ["Ignoring invalid completed charging session history"]
    assert "private-corrupt-history" not in caplog.text
    # The next ordinary intent save repairs the optional data once. A later
    # restart must not repeatedly warn or lose any accepted charging limits.
    store.save(state)
    assert json.loads(path.read_text())["charging_session_history"] == SessionHistory().snapshot()
    before = path.stat().st_mtime_ns
    store.save(state)
    assert path.stat().st_mtime_ns == before
    IntentStore(path, "wallbox").load(WriteState())
    assert len(caplog.messages) == 1


@pytest.mark.parametrize("broken", [
    {"version": 2}, {"identity": "other-wallbox"}, {"pause_requested": 1},
    {"charging_release_requested": 1}, {"charging_setpoint": 3},
    {"energy_goal": {}}, {"soc_goal": {}},
])
def test_optional_history_recovery_never_accepts_invalid_control_state(tmp_path, caplog, broken):
    path = tmp_path / "intent.json"
    payload = {
        "version": 1, "identity": "wallbox", "pause_requested": False,
        "charging_setpoint": 8, "charging_session_history": {"version": 2, "latest": None},
    } | broken
    path.write_text(json.dumps(payload))
    state = WriteState(pause_requested=True, charging_setpoint=6)
    assert IntentStore(path, "wallbox").load(state) is False
    assert state.pause_requested is True
    assert state.charging_setpoint == 6
    assert caplog.messages == []


def test_invalid_json_is_not_treated_as_optional_history_failure(tmp_path, caplog):
    path = tmp_path / "intent.json"
    path.write_text('{"version": 1, "charging_session_history":')
    state = WriteState(pause_requested=True, charging_setpoint=6)
    assert IntentStore(path, "wallbox").load(state) is False
    assert state.pause_requested is True
    assert state.charging_setpoint == 6
    assert caplog.messages == []


def test_invalid_optional_history_does_not_block_service_heartbeat(tmp_path, caplog):
    async def run():
        service, _transport = await owner(tmp_path)
        await service.stop()
        payload = json.loads(service.state_path.read_text())
        payload.update({
            "pause_requested": True, "charging_release_requested": False,
            "charging_setpoint": 8,
            "charging_session_history": {"version": 1, "latest": "private-corrupt-history"},
        })
        service.state_path.write_text(json.dumps(payload))
        replacement, transport = await owner(tmp_path, read_only=False)
        try:
            assert replacement.heartbeat.running
            assert replacement.snapshot()["available"]
            assert replacement.client.write_state.pause_requested is True
            assert replacement.client.write_state.charging_release_requested is False
            assert replacement.client.write_state.charging_setpoint == 8
            assert replacement.client.write_state.session_history.latest is None
            heartbeat = asyncio.Event()
            remove_listener = replacement.heartbeat.async_add_listener(heartbeat.set)
            if replacement.heartbeat.healthy is True:
                heartbeat.set()
            await asyncio.wait_for(heartbeat.wait(), timeout=1)
            remove_listener()
            assert replacement.diagnostics.heartbeats_sent > 0
            assert transport.writes
            assert all(address == R.HEARTBEAT.address for address, _ in transport.writes)
            assert caplog.text.count("Ignoring invalid completed charging session history") == 1
            assert "private-corrupt-history" not in caplog.text
        finally:
            await replacement.stop()

    asyncio.run(run())


def test_store_rejects_older_intent_without_current_metadata(tmp_path):
    path = Path(tmp_path) / "intent.json"
    path.write_text(json.dumps({"version": 1, "identity": "wallbox", "pause_requested": True}))
    state = WriteState()
    assert IntentStore(path, "wallbox").load(state) is False
    assert state.pause_requested is None
    assert state.session_history.latest is None


def test_cancelled_session_save_finishes_before_control_saves_can_replace_it(tmp_path):
    async def run():
        clock = [1000.0]
        with patch("custom_components.mennekes_amtron_rtu.device.service_runtime.wall_clock", side_effect=lambda: clock[0]):
            service, transport = await owner(tmp_path)
            real_save = service._store.save
            entered, release = threading.Event(), threading.Event()

            def blocked(state):
                entered.set()
                assert release.wait(5)
                real_save(state)

            try:
                await observe(service, transport, clock, 1005, EvseState.EV_CONNECTED, 100)
                await observe(service, transport, clock, 1010, EvseState.CHARGING, 100)
                await observe(service, transport, clock, 1100, EvseState.EV_CONNECTED, 112)
                service._store.save = blocked
                task = asyncio.create_task(observe(service, transport, clock, 1120, EvseState.IDLE, 112))
                assert await asyncio.to_thread(entered.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not service._session_history_dirty
                summary = service.snapshot()["charging_session"]["latest"]
                assert service._saved_session_history["latest"] == summary
                service._store.save = real_save
                await service._persist()
                saved = json.loads(await asyncio.to_thread(service.state_path.read_text))
                assert saved["charging_session_history"]["latest"] == summary
            finally:
                release.set()
                service._store.save = real_save
                await service.stop()

    asyncio.run(run())
