"""Rejected state stays quarantined until an explicit, non-starting reset."""
import asyncio
import json
import threading

import pytest
from aiohttp.test_utils import TestServer
from homeassistant.helpers import issue_registry as ir

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.const import DOMAIN
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    DeviceValidationError,
)
from custom_components.mennekes_amtron_rtu.device.service_departure_runtime import (
    DepartureModbusService,
)
from custom_components.mennekes_amtron_rtu.device.service_http import create_app
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from custom_components.mennekes_amtron_rtu.repairs import IntentResetRepairFlow
from tests import test_modbus_service as fixture_module
from tests.test_home_assistant_smoke import _boot
from tests.test_modbus_service import service_fixture
from tests.test_service_reauth import add_entry


def rejected_file(path):
    state = WriteState(pause_requested=True, charging_release_requested=False)
    state.plug_charge.configure(True, "a" * 64)
    IntentStore(path, "ABC123456789").save(state)
    raw = json.loads(path.read_text())
    raw["plug_charge"].pop("manual")
    path.write_text(json.dumps(raw))
    return path.read_bytes()


@pytest.mark.parametrize("initially_absent", [True, False])
def test_reset_keeps_release_off_after_vehicle_absence(tmp_path, monkeypatch, initially_absent):
    original_bank = fixture_module.device_bank

    def vehicle_bank(**kwargs):
        bank = original_bank(**kwargs)
        bank[R.EVSE_STATE.address] = 1 if initially_absent else 3
        bank[R.CP_STATE.address] = 10 if initially_absent else 30
        return bank

    monkeypatch.setattr(fixture_module, "device_bank", vehicle_bank)

    async def run():
        path = tmp_path / "intent.json"
        rejected_file(path)
        service, transport, clock = await service_fixture(tmp_path)
        try:
            if not initially_absent:
                transport.bank[R.EVSE_STATE.address] = 1
                transport.bank[R.CP_STATE.address] = 10
                clock[0] += 6
                await service.refresh()
            state = service.client.write_state
            limiter = state.limiter
            result = await service.command({
                "command": "acknowledge_intent_reset",
                "expected_serial_number": service.identity.serial_number,
            })
            assert not result["control"]["intent_rejected"]
            assert result["control"]["charging_release_requested"] is False
            assert service.control.charging_release_requested is False
            assert service.control.pause_requested is True
            assert service.control.charging_setpoint == 6
            assert service.client.write_state is state
            assert state.limiter is limiter
            await service.refresh()
            await service._persist()
            restored = WriteState()
            assert IntentStore(path, service.identity.serial_number).load(restored)
            assert restored.charging_release_requested is False
            assert restored.pause_requested is True
        finally:
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("read_only", [False, True])
def test_quarantine_survives_restart_and_never_overwrites_evidence(tmp_path, read_only):
    async def run():
        path = tmp_path / "intent.json"
        original = rejected_file(path)
        for _ in range(2):
            service, transport, _clock = await service_fixture(tmp_path, read_only=read_only)
            try:
                assert service.snapshot()["control"]["intent_rejected"]
                assert service.control.read_only
                if read_only:
                    assert transport.writes == []
                else:
                    assert any(address == R.CHARGING_RELEASE.address and words == [0] for address, words in transport.writes)
                for command in ("resume", "plug_charge_config"):
                    with pytest.raises(DeviceValidationError):
                        await service.command({"command": command, "value": False, "expected_serial_number": service.identity.serial_number})
                service.data.values["error_code"] = 200
                for _ in range(4):
                    await service._recover()
                    await service._enforce_current_cap()
                assert service._attempts == 0
                await service._persist()
                assert path.read_bytes() == original
            finally:
                await service.stop()
            assert path.read_bytes() == original
    asyncio.run(run())


@pytest.mark.parametrize("extended", [False, True])
def test_ha_repair_acknowledges_without_starting_and_clears_issue(tmp_path, monkeypatch, extended):
    if extended:
        monkeypatch.setattr(fixture_module, "ModbusService", DepartureModbusService)
    async def run():
        rejected_file(tmp_path / "intent.json")
        service, transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                issue = (DOMAIN, f"{entry.entry_id}_service_intent_rejected")
                assert issue in ir.async_get(hass).issues
                before = list(transport.writes)
                flow = IntentResetRepairFlow(entry.entry_id)
                flow.hass = hass
                assert (await flow.async_step_confirm())["type"] == "form"
                assert (await flow.async_step_confirm({}))["type"] == "create_entry"
                await hass.async_block_till_done()
                assert issue not in ir.async_get(hass).issues
                assert service.control.pause_requested is True
                assert service.control.charging_release_requested is False
                assert service.control.charging_setpoint == 6
                assert not service.control.read_only
                assert all(a == R.HEARTBEAT.address for a, _ in transport.writes[len(before):])
                restored = WriteState()
                assert IntentStore(tmp_path / "intent.json", service.identity.serial_number).load(restored)
                assert restored.pause_requested
                assert restored.charging_release_requested is False
        finally:
            await hass.async_stop()
            await service.stop()
    asyncio.run(run())


def test_acknowledgement_needs_identity_and_durable_save(tmp_path, monkeypatch):
    async def run():
        original = rejected_file(tmp_path / "intent.json")
        service, transport, _ = await service_fixture(tmp_path)
        try:
            payload = {"command": "acknowledge_intent_reset", "expected_serial_number": "wrong"}
            with pytest.raises(DeviceError, match="device_identity_changed"):
                await service.command(payload)
            payload["expected_serial_number"] = service.identity.serial_number
            with pytest.raises(ValueError, match="invalid reset acknowledgement"):
                await service.command({**payload, "value": True})
            real_save = service._store.save
            def failed(_state):
                raise OSError("storage unavailable")
            monkeypatch.setattr(service._store, "save", failed)
            with pytest.raises(OSError, match="storage unavailable"):
                await service.command(payload)
            assert service.control.read_only
            assert (tmp_path / "intent.json").read_bytes() == original
            monkeypatch.setattr(service._store, "save", real_save)
            await service.command(payload)
            service.control.pause_requested = False
            before = list(transport.writes)
            await service.command(payload)
            assert service.control.pause_requested is False
            assert transport.writes == before
        finally:
            await service.stop()
    asyncio.run(run())


def test_quarantine_does_not_replace_the_shared_write_guard(tmp_path):
    async def run():
        rejected_file(tmp_path / "intent.json")
        service, _transport, _clock = await service_fixture(tmp_path)
        try:
            state = service.client.write_state
            limiter = state.limiter
            generation = state.current_write_generation
            await service.heartbeat.async_stop()
            await service.control.async_shutdown()
            await service._initialize()
            assert service.client.write_state is state
            assert state.limiter is limiter
            assert state.current_write_generation >= generation
            assert service.control.read_only
        finally:
            await service.stop()
    asyncio.run(run())


def test_cancelled_reset_keeps_store_locked_until_the_save_finishes(tmp_path, monkeypatch):
    async def run():
        rejected_file(tmp_path / "intent.json")
        service, _transport, _clock = await service_fixture(tmp_path)
        entered, release = threading.Event(), threading.Event()
        original_save = service._store.save
        task = None

        def blocked(state):
            entered.set()
            assert release.wait(5)
            original_save(state)

        monkeypatch.setattr(service._store, "save", blocked)
        try:
            payload = {"command": "acknowledge_intent_reset", "expected_serial_number": service.identity.serial_number}
            task = asyncio.create_task(service.command(payload))
            assert await asyncio.to_thread(entered.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert service._store_lock.locked()
                assert service._command_lock.locked()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.control.read_only
            assert service._intent_rejected
            assert not service._store_lock.locked()
            monkeypatch.setattr(service._store, "save", original_save)
            await service.command(payload)
            assert not service._intent_rejected
            assert service.control.pause_requested
            assert service.control.charging_release_requested is False
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize("configured", ["plug_charge_off", "soc", "trip_disabled"])
@pytest.mark.parametrize("acknowledgement", ["repair", "external"])
def test_quarantine_does_not_block_ha_setup_with_existing_options(tmp_path, configured, acknowledgement):
    from homeassistant.config_entries import ConfigEntryState

    from tests.test_vehicle_profile_runtime import A, catalog

    async def run():
        rejected_file(tmp_path / "intent.json")
        service, _transport, _clock = await service_fixture(tmp_path)
        hass = await _boot(tmp_path / "ha")
        try:
            async with TestServer(create_app(service)) as server:
                entry = await add_entry(hass, str(server.make_url("/")))
                options = dict(entry.options)
                if configured == "plug_charge_off":
                    options["virtual_plug_charge"] = False
                else:
                    options.update(catalog())
                    if configured == "trip_disabled":
                        options["vehicle_profiles"][A]["options"]["timed_goal_enabled"] = False
                hass.config_entries.async_update_entry(entry, options=options)
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert (DOMAIN, f"{entry.entry_id}_service_intent_rejected") in ir.async_get(hass).issues
                if acknowledgement == "repair":
                    flow = IntentResetRepairFlow(entry.entry_id)
                    flow.hass = hass
                    assert (await flow.async_step_confirm({}))["type"] == "create_entry"
                else:
                    await service.command({"command": "acknowledge_intent_reset", "expected_serial_number": service.identity.serial_number})
                    await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert entry.state is ConfigEntryState.LOADED
                assert not service._intent_rejected
                assert service.control.pause_requested
                assert service.control.charging_release_requested is False
                if configured != "plug_charge_off":
                    assert entry.runtime_data.vehicle_soc is not None
        finally:
            await hass.async_stop()
            await service.stop()
    asyncio.run(run())
