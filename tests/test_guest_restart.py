"""Restart restores only a proven guest connection and all shared stop guards."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from tests import service_guest_fixture as runtime
from tests.fakes import put


@pytest.mark.parametrize("pnc", [False, True])
@pytest.mark.parametrize("paused", [False, True])
def test_restart_restores_same_connection_without_losing_limits_or_manual_pause(tmp_path, monkeypatch, pnc, paused):
    async def run():
        deadline = datetime.now(UTC) + timedelta(hours=1)
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            if pnc:
                await service.control.async_configure_plug_charge(True, policy="a" * 64)
            await service.departure.set_trip(deadline, "manual")
            await service.control.async_set_energy_goal(5)
            await service.control.async_configure_soc("car")
            await service.control.async_set_soc_goal(80, source="car", soc=90)
            clock[0] += 1000
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            if paused:
                clock[0] += 1000
                await service.command({"command": "pause", "expected_serial_number": service.identity.serial_number})
            bank = transport.bank
        async with runtime.guest_service(tmp_path, bank=bank) as (service, transport, clock):
            assert not service._intent_rejected
            assert service.guest.mode
            assert not service.guest.restore_pending
            assert service.guest_deadline == deadline
            assert service.control.energy_goal.target_kwh == 5
            assert service.control.soc_goal.target_percent == 80
            assert service.control.pause_requested is paused
            assert not service.control.goal_stop_requested
            await service.departure.tick()
            assert not transport.writes
            assert service.guest.active is not paused
            clock[0] += 10
            await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
            await service.guest.tick()
            assert service.control.pause_requested is True
    asyncio.run(run())


@pytest.mark.parametrize("change", ["counter", "missing-proof", "missing-plan", "damaged-plan", "unplugged"])
def test_restart_does_not_reuse_unproven_permission(tmp_path, monkeypatch, change):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            await service.departure.set_trip(datetime.now(UTC) + timedelta(hours=1), "manual")
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            bank = transport.bank
        if change == "counter":
            put(bank, R.SESSIONS_TOTAL, 43)
        elif change == "missing-proof":
            path = tmp_path / "intent.json"
            raw = json.loads(path.read_text())
            raw["guest_permission"]["session"] = None
            path.write_text(json.dumps(raw))
        elif change == "missing-plan":
            (tmp_path / "departure-plan.json").unlink()
        elif change == "damaged-plan":
            (tmp_path / "departure-plan.json").write_text("invalid")
        else:
            put(bank, R.EVSE_STATE, 1)
            put(bank, R.CP_STATE, 10)
        async with runtime.guest_service(tmp_path, bank=bank) as (service, transport, clock):
            if change == "unplugged":
                assert not service.guest.mode
                assert service.client.write_state.guest_permission is None
                return
            assert service.guest.restore_pending
            assert service.snapshot()["control"]["operation"]["waiting"] == "vehicle_unconfirmed"
            with pytest.raises(DeviceError):
                await service.control.async_resume_charging(current_cap=16)
            clock[0] += 10
            await service.guest.tick()
            assert runtime.current_writes(transport)[-1] < 6
            assert service.control.pause_requested
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] < 6
            clock[0] += 1000
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            assert not service.guest.restore_pending
            assert service.guest.active
    asyncio.run(run())


@pytest.mark.parametrize("expired", [False, True])
def test_restart_enforces_already_reached_limit(tmp_path, monkeypatch, expired):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            if expired:
                await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
            else:
                await service.control.async_set_energy_goal(1)
                service.control.energy_goal.observe(service.control.energy_goal.baseline_kwh + 1)
            await service.persist()
            bank = transport.bank
        async with runtime.guest_service(tmp_path, bank=bank) as (service, transport, clock):
            assert service.guest.mode
            assert not service.guest.restore_pending
            assert service.control.energy_goal_stop_requested
            clock[0] += 10
            await service.guest.tick()
            if not expired:
                await service.control.async_pause_for_energy_goal()
            assert runtime.current_writes(transport)[-1] < 6
    asyncio.run(run())


def test_guest_evidence_storage_is_strict_and_bounded(tmp_path):
    from custom_components.mennekes_amtron_rtu.device.guest_permission import (
        GuestPermission,
    )
    from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
    from custom_components.mennekes_amtron_rtu.device.write_state import WriteState

    assert GuestPermission.restore({"session": None, "deadline": None}) == GuestPermission(None, None)
    for raw in (None, [], {"extra": 1}, {"session": {}, "deadline": None}, {"session": None, "deadline": "invalid"}):
        with pytest.raises(ValueError, match="invalid"):
            GuestPermission.restore(raw)
    store = IntentStore(tmp_path / "intent.json", "wallbox")
    state = WriteState(guest_permission=GuestPermission(None, None))
    store.save(state)
    stamp = store.path.stat().st_mtime_ns
    for _ in range(10):
        store.save(state)
    assert store.path.stat().st_mtime_ns == stamp
    restored = WriteState()
    assert store.load(restored)
    assert restored.guest_permission == state.guest_permission
    raw = json.loads(store.path.read_text())
    raw["guest_permission"] = {"unexpected": True}
    store.path.write_text(json.dumps(raw))
    assert not store.load(restored)


@pytest.mark.parametrize("failure", ["store", "cancel", "release"])
def test_failed_start_before_acceptance_rolls_back_guest_permission(tmp_path, monkeypatch, failure):
    from unittest.mock import AsyncMock

    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            if failure == "store":
                real_persist = service.persist
                calls = 0

                async def fail_once(**kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        raise OSError("disk full")
                    await real_persist(**kwargs)
                monkeypatch.setattr(service, "persist", fail_once)
            else:
                method = "async_cancel_timed_current" if failure == "cancel" else "async_start_guest_charging"
                monkeypatch.setattr(service.control, method, AsyncMock(side_effect=DeviceError("guest_start_not_permitted")))
            with pytest.raises((OSError, DeviceError)):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest.revision})
            assert not transport.writes
            assert not service.guest.mode
            assert service.client.write_state.guest_permission is None
            assert json.loads((tmp_path / "intent.json").read_text())["guest_permission"] is None
    asyncio.run(run())


@pytest.mark.parametrize("validation", [False, True])
def test_failed_resume_after_accepted_release_keeps_guest_limits(tmp_path, monkeypatch, validation):
    from unittest.mock import AsyncMock

    from custom_components.mennekes_amtron_rtu.device.errors import (
        DeviceValidationError,
    )

    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            error = DeviceValidationError("guest_start_not_permitted") if validation else DeviceError("service_unavailable")
            monkeypatch.setattr(service.control.writes, "async_apply_current", AsyncMock(side_effect=error))
            with pytest.raises(DeviceError):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest.revision})
            assert service.guest.mode
            assert service.client.write_state.guest_permission is not None
    asyncio.run(run())


@pytest.mark.parametrize("uncertain", [False, True])
def test_release_reply_failure_keeps_permission_only_when_write_may_have_happened(tmp_path, monkeypatch, uncertain):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            put(transport.bank, R.CHARGING_RELEASE, 0)
            await service.refresh()
            transport.write_exceptions[R.CHARGING_RELEASE.address] = TimeoutError() if uncertain else 3
            clock[0] += 1000
            with pytest.raises((DeviceError, TimeoutError)):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest.revision})
            assert service.guest.mode is uncertain
            assert (service.client.write_state.guest_permission is not None) is uncertain
    asyncio.run(run())
