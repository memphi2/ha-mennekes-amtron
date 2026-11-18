"""A guest button uses shared limits without importing a vehicle's target."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from tests import service_guest_fixture as runtime
from tests.fakes import put


@pytest.mark.parametrize("pnc", [False, True])
@pytest.mark.parametrize("bounded", [False, True])
def test_guest_starts_from_pause_and_keeps_shared_goals(tmp_path, monkeypatch, pnc, bounded):
    async def body():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            control = service.control
            if pnc:
                await control.async_configure_plug_charge(True, policy="a" * 64)
            await control.async_configure_soc("car")
            await control.async_set_soc_goal(80, source="car", soc=90)
            await asyncio.sleep(0)
            clock[0] += 1000
            await control.async_pause_charging()
            deadline = datetime.now(UTC) + timedelta(hours=1) if bounded else None
            await service.departure.set_trip(deadline, "manual")
            if bounded:
                await control.async_set_energy_goal(5)
            energy = control.energy_goal.snapshot()
            clock[0] += 1000
            await service.refresh()
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest.revision})
            await service.refresh()
            await service.guest.tick()
            assert service.guest_deadline == deadline
            assert service.guest.active
            assert control.soc_goal.target_percent == 80
            assert control.energy_goal.snapshot() == energy
            assert control.pause_requested is False
            assert not (tmp_path / "guest-charge.json").exists()
            assert not (tmp_path / "charge-rates.json").exists()
            assert runtime.current_writes(transport)[-1] >= 6
            assert control.plug_charge["enabled"] is pnc
            if bounded:
                await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
                clock[0] += 1000
                await service.guest.tick()
                assert not service.guest.active
                assert runtime.current_writes(transport)[-1] < 6
            put(transport.bank, R.EVSE_STATE, 1)
            put(transport.bank, R.CP_STATE, 10)
            await service.refresh()
            assert not control.guest_active()
    asyncio.run(body())


@pytest.mark.parametrize("denial", ["stale", "identity", "readonly", "lock", "fault", "expired"])
def test_guest_refusal_never_grants_release(tmp_path, monkeypatch, denial):
    from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
    async def body():
        async with runtime.guest_service(tmp_path) as (service, transport, _clock):
            payload = {"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                       "expected_guest_revision": service.guest.revision}
            if denial == "stale":
                payload["expected_guest_revision"] = "stale"
            elif denial == "identity":
                payload["expected_serial_number"] = "other"
            elif denial == "readonly":
                service.control._options = replace(service.control._options, read_only=True)
            elif denial in ("lock", "fault"):
                put(transport.bank, R.LOCK_EVSE if denial == "lock" else R.ERROR_CODE, 1)
                await service.refresh()
            else:
                await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
                payload["expected_guest_revision"] = service.guest.revision
            before = list(transport.writes)
            with pytest.raises(DeviceError):
                await service.command(payload)
            assert transport.writes == before
            assert service.guest.scope is None
    asyncio.run(body())


def test_guest_uses_existing_energy_baseline_and_stops_before_time(tmp_path, monkeypatch):
    async def body():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            control = service.control
            await control.async_set_energy_goal(1)
            baseline = control.energy_goal.baseline_kwh
            await service.departure.set_trip(datetime.now(UTC) + timedelta(hours=1), "manual")
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            assert control.energy_goal.baseline_kwh == baseline
            clock[0] += 1000
            put(transport.bank, R.ENERGY_TOTAL, baseline + 1.1)
            service._poller._slow_read_at = None
            await service.refresh()
            await service.guest.tick()
            if control.goal_controller._task is not None:
                await control.goal_controller._task
            assert not service.guest.active
            assert runtime.current_writes(transport)[-1] < 6
    asyncio.run(body())


def test_guest_uses_normal_current_and_pause_without_planner_takeover(tmp_path, monkeypatch):
    from tests.test_departure_planner import hand_over
    async def body():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            await hand_over(service)
            await service.departure.tick()
            clock[0] += 1000
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest.revision})
            clock[0] += 1000
            await service.command({"command":"set_current", "value":10, "expected_serial_number":service.identity.serial_number})
            assert runtime.current_writes(transport)[-1] == 10
            clock[0] += 1000
            await service.command({"command":"pause", "expected_serial_number":service.identity.serial_number})
            service.departure._last_write_at = None
            await service.departure.tick()
            assert runtime.current_writes(transport)[-1] < 6
            assert service.control.pause_requested
            assert not service.guest.active
    asyncio.run(body())


def test_old_guest_file_cannot_block_normal_control(tmp_path, monkeypatch):
    async def body():
        (tmp_path / "guest-charge.json").write_text("broken obsolete file")
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            clock[0] += 1000
            await service.command({"command":"set_current", "value":10, "expected_serial_number":service.identity.serial_number})
            assert runtime.current_writes(transport)[-1] == 10
    asyncio.run(body())


@pytest.mark.parametrize("withdraw", [False, True])
def test_vehicle_handover_does_not_replace_guest_time(tmp_path, monkeypatch, withdraw):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            deadline = datetime.now(UTC) + timedelta(hours=1)
            await service.departure.set_trip(deadline, "manual")
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest.revision})
            await service.command({
                "command":"departure_trip", "expected_serial_number":service.identity.serial_number,
                "deadline":None if withdraw else (deadline + timedelta(hours=2)).isoformat(),
                "source":"other-vehicle", "withdraw":withdraw,
            })
            assert service.guest_deadline == deadline
            assert service.guest.active
            # The user can still change or clear that same time explicitly.
            for selected in (deadline + timedelta(minutes=10), None):
                await service.command({
                    "command":"departure_choice", "expected_serial_number":service.identity.serial_number,
                    "deadline":None if selected is None else selected.isoformat(),
                    "revision":service.snapshot()["control"]["target_time_revision"],
                })
                assert service.guest_deadline == selected
    asyncio.run(run())


def test_guest_time_stop_keeps_write_floor_without_state_change_delay(tmp_path, monkeypatch):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            await service.control.async_pause_charging()
            clock[0] += 1000
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest.revision})
            await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
            # The time limit must not enqueue a manual pause for five minutes.
            clock[0] += 10
            await service.guest.tick()
            assert runtime.current_writes(transport)[-1] < 6
            assert service.control.pause_requested is True
            assert service.control.pending_action is None
            assert not service.guest.active
            assert service.snapshot()["control"]["operation"]["waiting"] == "target_reached"
    asyncio.run(run())


def test_time_stop_retains_five_second_floor_and_clearing_does_not_leave_a_pause_queued(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.client_errors import (
        AmtronRateLimitedError,
    )
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            await service.control.async_pause_charging()
            clock[0] += 1000
            await service.command({"command":"guest_start", "expected_serial_number":service.identity.serial_number,
                                   "expected_guest_revision":service.guest.revision})
            await service.departure.set_trip(datetime.now(UTC) - timedelta(seconds=1), "manual")
            before = list(transport.writes)
            clock[0] += 1
            with pytest.raises(AmtronRateLimitedError):
                await service.guest.tick()
            assert transport.writes == before
            assert service.guest.active
            assert service.control.pending_action is None
            await service.departure.set_trip(None, "manual")
            clock[0] += 10
            await service.guest.tick()
            assert transport.writes == before
    asyncio.run(run())


def test_unknown_stop_readback_is_not_reported_as_confirmed_pause(tmp_path):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            service.control.pause_requested = True
            service.client.write_state.confirmed_current = None
            service.data.values["charging_current_ems"] = None
            assert service.control.is_paused is None
            assert service.guest.active
            service.data.values["charging_current_ems"] = 1.0
            assert service.control.is_paused is True
            assert not service.guest.active
    asyncio.run(run())


@pytest.mark.parametrize("pnc", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_guest_release_and_resume_remain_one_deferred_request(tmp_path, monkeypatch, pnc, cancel):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            if pnc:
                await service.control.async_configure_plug_charge(True, policy="a" * 64)
            put(transport.bank, R.CHARGING_RELEASE, 0)
            await service.refresh()
            service.client.write_state.mode_change_at = clock[0]
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            assert service.control.pending_action == "resume"
            assert service.control.pending_guest_start
            assert service.data.get("charging_release") == 0
            # Waiting is not an applied authorization. Cancellation or error
            # recovery must not inherit a manual Plug & Charge bypass.
            assert service.control.plug_charge["manual"] is False
            if cancel:
                await service.control.async_cancel_pending_change()
            clock[0] += 301
            service.control._pending_changed.set()
            await asyncio.sleep(0.05)
            await service.refresh()
            assert service.data.get("charging_release") == (0 if cancel else 1)
            assert service.control.pending_action is None
            if not cancel:
                assert runtime.current_writes(transport)[-1] >= 6
    asyncio.run(run())


def test_queued_guest_start_cannot_release_another_plug_in(tmp_path, monkeypatch):
    async def run():
        async with runtime.guest_service(tmp_path) as (service, transport, clock):
            put(transport.bank, R.CHARGING_RELEASE, 0)
            await service.refresh()
            service.client.write_state.mode_change_at = clock[0]
            await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                   "expected_guest_revision": service.guest.revision})
            assert service.control.pending_guest_start
            put(transport.bank, R.EVSE_STATE, 1)
            put(transport.bank, R.CP_STATE, 10)
            await service.refresh()
            put(transport.bank, R.EVSE_STATE, 2)
            put(transport.bank, R.CP_STATE, 20)
            await service.refresh()
            clock[0] += 301
            service.control._pending_changed.set()
            await asyncio.sleep(0.05)
            assert not service.guest.mode
            assert service.control.pending_action is None
            assert service.data.get("charging_release") == 0
    asyncio.run(run())


def test_failure_after_an_applied_guest_release_is_marked_uncertain(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.errors import (
        DeviceError,
        DeviceValidationError,
        validation_error,
    )

    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            control = service.control

            async def release_then_stop(**_kwargs):
                control.events.record("executed", "enable_charging")
                raise validation_error("energy_goal_stop_active")

            monkeypatch.setattr(control, "async_start_guest_charging", release_then_stop)
            with pytest.raises(DeviceError) as caught:
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest.revision})
            # Every caller, e.g. HA over HTTP, must see a change, not a refusal.
            assert not isinstance(caught.value, DeviceValidationError)
            assert caught.value.translation_key == "guest_start_uncertain"
            assert service.guest.scope is not None
            # A later start refused before any write is a definite refusal again.
            with pytest.raises(DeviceError, match="soc_goal_source_changed"):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": "stale"})
    asyncio.run(run())


def test_guest_refusal_after_cancelling_a_queued_departure_is_uncertain(tmp_path, monkeypatch):
    from custom_components.mennekes_amtron_rtu.device.errors import (
        DeviceError,
        validation_error,
    )

    async def run():
        async with runtime.guest_service(tmp_path) as (service, _transport, _clock):
            control = service.control
            monkeypatch.setattr(type(control), "pending_owner", property(lambda _self: "departure"))

            async def refused(**_kwargs):
                raise validation_error("energy_goal_stop_active")

            monkeypatch.setattr(control, "async_start_guest_charging", refused)
            with pytest.raises(DeviceError, match="guest_start_uncertain"):
                await service.command({"command": "guest_start", "expected_serial_number": service.identity.serial_number,
                                       "expected_guest_revision": service.guest.revision})
    asyncio.run(run())
