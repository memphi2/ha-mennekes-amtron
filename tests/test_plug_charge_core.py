"""The bus owner grants only a current plug cycle and retains independent stops."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.control import (
    AmtronControl,
    ControlOptions,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
    EnergyGoalController,
)
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceError,
    DeviceValidationError,
)
from custom_components.mennekes_amtron_rtu.device.plug_charge import PlugChargeState
from custom_components.mennekes_amtron_rtu.device.plug_cycle import PlugCycle
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.test_control import Harness
from tests.test_modbus_service import service_fixture

POLICY = "a" * 64


def unplugged(control):
    control.observe_plug(WallboxData(values={
        "evse_state": int(EvseState.IDLE), "cp_state": int(CpState.A1), "charging_release": 1,
    }))


def present(harness, connected=True):
    values = {
        "evse_state": int(EvseState.EV_CONNECTED if connected else EvseState.IDLE),
        "cp_state": int(CpState.B1 if connected else CpState.A1),
        "charging_release": harness.transport.bank[R.CHARGING_RELEASE.address],
    }
    for spec in (R.EVSE_STATE, R.CP_STATE):
        harness.transport.bank[spec.address] = values[spec.key]
    harness.control.observe_plug(WallboxData(values=values))


async def armed(harness):
    control = await harness.connect()
    await control.async_configure_plug_charge(True, policy=POLICY)
    present(harness)
    assert control.plug_charge["session"]
    return control


def deadline():
    return datetime.now(UTC) + timedelta(seconds=10)


async def authorize(control):
    await control.async_authorize_plug_charge(control.plug_charge["session"], deadline=deadline())


def see(state, cycle, present, **when):
    """One piece of evidence for the plug cycle and the grant that follows it."""
    state.follow(cycle, cycle.observe(present, **when))


def test_cycle_and_policy_bindings_have_no_durable_grants():
    state, cycle = PlugChargeState(), PlugCycle()
    state.configure(True, POLICY)
    before_absence = datetime.now(UTC)
    see(state, cycle, False, at=before_absence)
    see(state, cycle, True)
    token = state.session(cycle)
    assert len(token) == 32
    assert before_absence == cycle.since
    state.authorized, state.hold = True, False
    see(state, cycle, True)
    state.configure(True, POLICY)
    assert state.session(cycle) == token
    assert state.authorized
    restored = PlugChargeState.restore(state.durable())
    assert restored.enabled
    assert restored.blocked
    assert restored.snapshot(PlugCycle())["session"] is None
    assert restored.snapshot(PlugCycle())["since"] is None
    # Another policy ends the grant; the connection is still the same one.
    state.configure(True, "b" * 64)
    assert state.blocked
    assert not state.authorized
    assert state.session(cycle) not in (None, token)
    token = state.session(cycle)
    # An unknown state is no unplug: the connection and its start remain.
    since = cycle.since
    see(state, cycle, None)
    assert (state.session(cycle), cycle.since) == (token, since)
    # Only a confirmed unplug ends it; the next plug-in is another one.
    see(state, cycle, False)
    assert state.session(cycle) is None
    see(state, cycle, True)
    assert state.session(cycle) not in (None, token)
    state.configure(False, "")
    assert state.blocked
    assert not state.enabled
    # Without the gate there is no session to grant.
    assert state.snapshot(cycle)["session"] is None
    assert state.snapshot(cycle)["since"] is None
    with pytest.raises(ValueError, match="saved"):
        PlugChargeState.restore({**state.durable(), "authorized": True})


@pytest.mark.parametrize("policy", ["x", "a" * 32, "A" * 64, None])
def test_policy_validation(policy):
    with pytest.raises(ValueError, match="configuration"):
        PlugChargeState().configure(True, policy)


def test_gate_closes_without_changing_manual_intent_and_grants_only_release():
    async def run():
        h = Harness()
        control = await armed(h)
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert control.charging_release_requested is None
        assert control.pause_requested is None
        releases = control._release_writes
        await authorize(control)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        # The grant is a release like one by hand to everything that counts them.
        assert control._release_writes == releases + 1
        assert control.plug_charge["authorized"]
        assert not control.plug_charge["manual"]
        assert h.written(R.CHARGING_CURRENT_EMS) == []
        assert h.written(R.LOCK_EVSE) == []
        assert control.charging_release_requested is None
        await control.async_configure_plug_charge(True, policy=POLICY)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        present(h)
        await asyncio.sleep(0)
        assert control.plug_charge["authorized"]
        await control.async_shutdown()
    asyncio.run(run())


def test_a_manual_release_opens_the_gate_for_this_connection_only():
    async def run():
        h = Harness()
        control = await armed(h)
        await control.async_set_charging_release(True)
        assert control.charging_release_requested is True
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        assert control.plug_charge["manual"]
        assert not control.plug_charge["authorized"]
        # A poll of the open release is no reason to close it again.
        present(h)
        await asyncio.sleep(0)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        # The unplug ends the manual release; the gate closes again.
        h.transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
        h.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        unplugged(control)
        await asyncio.sleep(0)
        await control.async_sync_plug_charge()
        assert not control.plug_charge["manual"]
        assert h.written(R.CHARGING_RELEASE)[-1] == [0]
        await control.async_shutdown()
    asyncio.run(run())



def test_a_release_switched_off_by_hand_stops_only_this_connection():
    async def run():
        h = Harness()
        control = await armed(h)
        await authorize(control)
        await control.async_set_charging_release(False)
        assert control.charging_release_requested is False
        assert h.written(R.CHARGING_RELEASE)[-1] == [0]
        # Unknown readings are no unplug and keep the stop.
        control.observe_plug(None)
        assert control.charging_release_requested is False
        # The unplug ends it: the next recognized plug-in can be granted.
        h.transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
        h.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        unplugged(control)
        assert control.charging_release_requested is None
        present(h)
        await authorize(control)
        assert control.plug_charge["authorized"]
        assert h.written(R.CHARGING_RELEASE)[-1] == [1]
        await control.async_shutdown()
    asyncio.run(run())


def test_enabling_plug_and_charge_forgets_an_old_release_stop():
    async def run():
        h = Harness()
        control = await h.connect()
        control.charging_release_requested = False
        await control.async_configure_plug_charge(True, policy=POLICY)
        assert control.charging_release_requested is None
        # Without Plug & Charge, a stop is kept, also after an unplug.
        await control.async_configure_plug_charge(False)
        control.charging_release_requested = False
        unplugged(control)
        assert control.charging_release_requested is False
        await control.async_shutdown()
    asyncio.run(run())

def test_a_manual_release_without_a_car_waits_for_the_next_plug_in():
    async def run():
        h = Harness()
        control = await h.connect()
        await control.async_configure_plug_charge(True, policy=POLICY)
        h.transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
        h.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        unplugged(control)
        await control.async_sync_plug_charge()
        await control.async_set_charging_release(True)
        assert h.written(R.CHARGING_RELEASE)[-1] == [1]
        # Repeated empty polls and an unknown state keep it armed.
        unplugged(control)
        control.observe_plug(None)
        await asyncio.sleep(0)
        assert control.plug_charge["manual"]
        assert h.written(R.CHARGING_RELEASE)[-1] == [1]
        present(h)
        assert control.plug_charge["manual"]
        assert control.plug_charge["session"]
        # Switching the release off withdraws it.
        await control.async_set_charging_release(False)
        assert not control.plug_charge["manual"]
        assert h.written(R.CHARGING_RELEASE)[-1] == [0]
        await control.async_shutdown()
    asyncio.run(run())


def test_a_manual_release_survives_a_restart_and_ends_with_the_gate():
    state = PlugChargeState()
    state.configure(True, POLICY)
    state.manual = True
    restored = PlugChargeState.restore(state.durable())
    assert restored.manual
    assert not restored.blocked
    # Older saved states have no manual release.
    legacy = {key: value for key, value in state.durable().items() if key != "manual"}
    with pytest.raises(ValueError, match="invalid saved"):
        PlugChargeState.restore(legacy)
    with pytest.raises(ValueError, match="saved"):
        PlugChargeState.restore({**state.durable(), "manual": 1})
    # A long unknown state or switching the gate off withdraws it.
    state.revoke()
    assert not state.manual
    state.manual = True
    state.configure(False, "")
    assert not state.manual


def test_a_grant_keeps_an_existing_pause_without_starting_a_charge():
    async def run():
        h = Harness()
        c = await armed(h)
        c.pause_requested = True
        await authorize(c)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        assert c.plug_charge["authorized"]
        assert c.pause_requested is True
        assert not h.written(R.CHARGING_CURRENT_EMS)
        await c.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("reason", ["release", "lock", "rfid", "error", "unsupported", "goal"])
def test_independent_stops_prevent_automatic_release(reason):
    async def run():
        h = Harness()
        c = await armed(h)
        if reason == "release":
            c.charging_release_requested = False
        elif reason == "lock":
            h.transport.bank[R.LOCK_EVSE.address] = 1
        elif reason == "rfid":
            h.transport.bank[R.AUTHORIZATION_ENABLED.address] = 1
        elif reason == "error":
            h.transport.bank[R.ERROR_CODE.address] = 200
        elif reason == "unsupported":
            h.identity.layout_version = R.LAYOUT_V01_01
        else:
            c.energy_goal._status = "reached"
        with pytest.raises(DeviceValidationError):
            await authorize(c)
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_read_only_cannot_enable_gate():
    async def run():
        h = Harness(read_only=True)
        c = await h.connect()
        with pytest.raises(DeviceValidationError) as exc:
            await c.async_configure_plug_charge(True, policy=POLICY)
        assert exc.value.translation_key == "read_only_mode"
        assert not c.plug_charge["enabled"]
        assert h.transport.writes == []
    asyncio.run(run())


@pytest.mark.parametrize("lost", ["unplug", "policy"])
def test_lost_physical_cycle_rejects_old_authorization(lost):
    async def run():
        h = Harness()
        c = await armed(h)
        token = c.plug_charge["session"]
        await authorize(c)
        if lost == "unplug":
            present(h, False)
        else:
            await c.async_configure_plug_charge(True, policy="b" * 64)
        await c.async_sync_plug_charge()
        present(h)
        with pytest.raises(DeviceValidationError) as exc:
            await c.async_authorize_plug_charge(token, deadline=deadline())
        assert exc.value.translation_key == "plug_charge_session_changed"
        assert h.written(R.CHARGING_RELEASE) == [[0], [1], [0]]
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_disabling_never_releases_and_manual_release_clears_hold():
    async def run():
        h = Harness()
        c = await armed(h)
        await c.async_configure_plug_charge(False)
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert c.write_state.plug_charge.hold
        await c.async_set_charging_release(True)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        assert not c.write_state.plug_charge.hold
        await c.async_shutdown()
    asyncio.run(run())


def test_absent_gate_close_does_not_impose_a_new_vehicle_delay():
    async def run():
        h = Harness(state_change_interval_seconds=300)
        c = await h.connect()
        present(h, False)
        await c.async_configure_plug_charge(True, policy=POLICY)
        assert c.write_state.mode_change_at is None
        present(h)
        await authorize(c)
        assert c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_connected_gate_close_honors_start_hysteresis_without_queuing():
    async def run():
        h = Harness(state_change_interval_seconds=300)
        c = await h.connect()
        present(h)
        await c.async_configure_plug_charge(True, policy=POLICY)
        present(h)
        with pytest.raises(DeviceValidationError) as exc:
            await authorize(c)
        assert exc.value.translation_key == "write_rate_limited"
        assert c.pending_action is None
        h.clock[0] += 300
        await authorize(c)
        assert c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("invalid", ["expired", "future", "naive", "wrong_token"])
def test_invalid_automatic_authorization_never_writes(invalid):
    async def run():
        h = Harness()
        c = await armed(h)
        until = deadline()
        token = c.plug_charge["session"]
        if invalid == "expired":
            until -= timedelta(seconds=11)
        elif invalid == "future":
            until += timedelta(seconds=60)
        elif invalid == "naive":
            until = until.replace(tzinfo=None)
        else:
            token = "b" * 32
        with pytest.raises(DeviceValidationError):
            await c.async_authorize_plug_charge(token, deadline=until)
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await c.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("race", ["unplug", "deadline", "goal"])
def test_final_live_reads_cannot_cross_a_stop_or_expired_request(monkeypatch, race):
    async def run():
        h = Harness()
        c = await armed(h)
        original = h.transport.read_holding_registers
        count = 0
        async def read(address, **kwargs):
            nonlocal count
            result = await original(address, **kwargs)
            if address == R.AUTHORIZATION_ENABLED.address:
                count += 1
                if count == 2:  # The actual bus-write precondition, not the early check.
                    if race == "unplug":
                        unplugged(c)
                    elif race == "goal":
                        c.energy_goal._status = "reached"
                    else:
                        await asyncio.sleep(0.04)
            return result
        monkeypatch.setattr(h.transport, "read_holding_registers", read)
        with pytest.raises(DeviceValidationError):
            await c.async_authorize_plug_charge(c.plug_charge["session"], deadline=datetime.now(UTC) + timedelta(seconds=0.03))
        assert [1] not in h.written(R.CHARGING_RELEASE)
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_service_persists_policy_but_new_process_requires_new_authorization(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        try:
            transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
            await service.command({"command": "plug_charge_config", "value": True, "policy": POLICY, "expected_serial_number": service.identity.serial_number})
            snap = service.snapshot()["control"]
            assert snap["plug_charge"]["session"]
            saved = json.loads((tmp_path / "intent.json").read_text())["plug_charge"]
            assert saved == {
                "enabled": True, "policy": POLICY, "hold": True, "manual": False, "manual_connected": False,
            }
            restored = WriteState()
            IntentStore(tmp_path / "intent.json", service.identity.serial_number).load(restored)
            assert restored.plug_charge.enabled
            assert restored.plug_charge.blocked
            # The connection is never saved: a restart has to see it again.
            assert restored.plug_cycle.id is None
            assert restored.plug_charge.snapshot(restored.plug_cycle)["since"] is None
        finally:
            await service.stop()
        restarted, transport, _clock = await service_fixture(tmp_path)
        try:
            assert transport.bank[R.CHARGING_RELEASE.address] == 0
            assert not restarted.control.plug_charge["authorized"]
        finally:
            await restarted.stop()
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["cancel", "lost_ack"])
def test_uncertain_authorization_is_closed_and_never_replayed(monkeypatch, failure):
    async def run():
        h = Harness()
        c = await armed(h)
        original = h.transport.write_register
        async def write(address, value, **kwargs):
            result = await original(address, value, **kwargs)
            if address == R.CHARGING_RELEASE.address and value == 1:
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise TimeoutError("acknowledgement lost")
            return result
        monkeypatch.setattr(h.transport, "write_register", write)
        expected = asyncio.CancelledError if failure == "cancel" else Exception
        with pytest.raises(expected):
            await authorize(c)
        await c.async_sync_plug_charge()
        assert h.written(R.CHARGING_RELEASE) == [[0], [1], [0]]
        # Only a fresh grant may release again, never the uncertain one.
        assert not c.plug_charge["authorized"]
        assert c.pending_action is None
        await c.async_shutdown()
    asyncio.run(run())


def test_deadline_is_rechecked_after_waiting_for_the_control_lock():
    async def run():
        h = Harness()
        c = await armed(h)
        async with c.energy_goal_guard:
            task = asyncio.create_task(c.async_authorize_plug_charge(c.plug_charge["session"], deadline=datetime.now(UTC) + timedelta(seconds=0.01)))
            await asyncio.sleep(0.02)
        with pytest.raises(DeviceValidationError) as exc:
            await task
        assert exc.value.translation_key == "plug_charge_authorization_expired"
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await c.async_shutdown()
    asyncio.run(run())


def test_manual_release_waiting_for_bus_cannot_bypass_a_lost_gate_grant(monkeypatch):
    async def run():
        h = Harness()
        c = await armed(h)
        await authorize(c)
        h.transport.bank[R.CHARGING_RELEASE.address] = 0
        original = h.transport.read_holding_registers
        changed = False
        async def read(address, **kwargs):
            nonlocal changed
            result = await original(address, **kwargs)
            if address == R.CHARGING_RELEASE.address and not changed:
                changed = True
                unplugged(c)
            return result
        monkeypatch.setattr(h.transport, "read_holding_registers", read)
        with pytest.raises(DeviceValidationError) as exc:
            await c.async_set_charging_release(True)
        assert exc.value.translation_key == "plug_charge_blocked"
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        await c.async_shutdown()
    asyncio.run(run())


def test_late_deny_task_does_not_close_a_newly_authorized_cycle():
    async def run():
        h = Harness()
        c = await armed(h)
        c.observe_plug(None)  # A deny worker exists but has not run yet.
        present(h)
        await authorize(c)
        await asyncio.sleep(0)
        await c.async_sync_plug_charge()
        assert c.plug_charge["authorized"]
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        await c.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("disabled", [False, True])
def test_heartbeat_recovery_keeps_ungranted_or_disabled_hold_closed(disabled):
    async def run():
        h = Harness(error_code=200)
        c = await armed(h)
        if disabled:
            await c.async_configure_plug_charge(False)
        await c.async_recover_from_error()
        assert h.written(R.HEARTBEAT)
        assert h.written(R.CHARGING_RELEASE) == [[0], [0]]
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_service_accepts_only_current_session_authorization(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        serial = service.identity.serial_number
        try:
            transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
            await service.command({"command": "plug_charge_config", "value": True, "policy": POLICY, "expected_serial_number": serial})
            session = service.control.plug_charge["session"]
            clock[0] += 301
            result = await service.command({"command": "plug_charge_authorize", "session": session, "deadline": deadline().isoformat(), "expected_serial_number": serial})
            assert result["control"]["plug_charge"]["authorized"]
            stored = json.loads((tmp_path / "intent.json").read_text())["plug_charge"]
            assert set(stored) == {"enabled", "policy", "hold", "manual", "manual_connected"}
            assert stored["hold"] is False
            for payload in (
                {"command": "plug_charge_config", "value": 1},
                {"command": "plug_charge_config", "value": True, "policy": "garbage"},
                {"command": "plug_charge_authorize", "session": session, "deadline": "invalid"},
                {"command": "plug_charge_authorize", "session": session},
            ):
                with pytest.raises(ValueError, match=r"[Ii]nvalid"):
                    await service.command({**payload, "expected_serial_number": serial})
        finally:
            await service.stop()
    asyncio.run(run())


def test_denial_retries_without_repeated_logs_and_stops_on_unload(caplog):
    async def run():
        h = Harness()
        c = await armed(h)
        await authorize(c)
        h.transport.write_exceptions[R.CHARGING_RELEASE.address] = TimeoutError("unavailable")
        for _ in range(2):
            unplugged(c)
            await c._plug_charge._task
        assert caplog.text.count("Plug & Charge: release gate cannot be enforced") == 1
        h.transport.write_exceptions.clear()
        unplugged(c)
        await c._plug_charge._task
        assert h.transport.bank[R.CHARGING_RELEASE.address] == 0
        await c.async_shutdown()
        unplugged(c)
        snapshot = c.plug_charge
        present(h)
        # The stopped gate grants and denies nothing any more.
        assert {**c.plug_charge, "session": None, "since": None} == snapshot
    asyncio.run(run())


@pytest.mark.parametrize("change", ["disabled", "unplug", "read_error", "bad_token"])
def test_live_authorization_failures_do_not_leave_a_grant(change):
    async def run():
        h = Harness()
        c = await armed(h)
        token = c.plug_charge["session"]
        if change == "disabled":
            await c.async_configure_plug_charge(False)
        elif change == "unplug":
            h.transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
            h.transport.bank[R.CP_STATE.address] = int(CpState.A1)
        elif change == "read_error":
            h.transport.read_exceptions[R.AUTHORIZATION_ENABLED.address] = "error"
        else:
            token = "malformed"  # noqa: S105 - deliberately invalid session fixture
        with pytest.raises(DeviceValidationError):
            await c.async_authorize_plug_charge(token, deadline=deadline())
        assert not c.plug_charge["authorized"]
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await c.async_shutdown()
    asyncio.run(run())


def test_already_released_hardware_still_requires_and_accepts_fresh_authorization():
    async def run():
        h = Harness()
        c = await armed(h)
        h.transport.bank[R.CHARGING_RELEASE.address] = 1
        await authorize(c)
        assert c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_deadline_expiring_during_write_ack_closes_uncertain_release(monkeypatch):
    async def run():
        h = Harness()
        c = await armed(h)
        original = h.transport.write_register
        async def write(address, value, **kwargs):
            result = await original(address, value, **kwargs)
            if address == R.CHARGING_RELEASE.address and value == 1:
                await asyncio.sleep(0.04)
            return result
        monkeypatch.setattr(h.transport, "write_register", write)
        with pytest.raises(DeviceValidationError) as exc:
            await c.async_authorize_plug_charge(c.plug_charge["session"], deadline=datetime.now(UTC) + timedelta(seconds=0.03))
        assert exc.value.translation_key == "plug_charge_authorization_expired"
        await c.async_sync_plug_charge()
        assert h.written(R.CHARGING_RELEASE) == [[0], [1], [0]]
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("link_lost", [False, True])
@pytest.mark.parametrize("granted", [False, True])
def test_control_handoff_preserves_session_only_while_transport_continues(link_lost, granted):
    async def run():
        h = Harness()
        old = await h.connect()
        await old.async_configure_plug_charge(True, policy=POLICY)
        absent_at = datetime.now(UTC) - timedelta(seconds=2)
        old.observe_plug(WallboxData(values={"evse_state": int(EvseState.IDLE), "cp_state": int(CpState.A1), "charging_release": 0}), observed_at=absent_at)
        present(h)
        if granted:
            await authorize(old)
        prior = old.plug_charge
        await old.async_shutdown()
        if link_lost:
            h.client._connection_changed(False)
        fresh = AmtronControl(
            h.client, diagnostics=h.diagnostics, identity=h.identity,
            options=ControlOptions(False, None, state_change_interval_seconds=0),
            state=lambda: h.snapshot, monotonic=lambda: h.clock[0],
        )
        await h.client.async_connect()
        await fresh.async_configure_plug_charge(True, policy=POLICY)
        h.control = fresh
        present(h)
        # A reconnect proves no unplug; the reads after it keep the connection.
        assert fresh.plug_charge == prior
        assert fresh.plug_charge["since"] == absent_at.isoformat()
        await fresh.async_authorize_plug_charge(prior["session"], deadline=deadline())
        assert fresh.plug_charge["authorized"]
        assert h.client.connected
        assert h.transport.close_calls == 0
        await fresh.async_shutdown()
        await h.client.async_close()
        assert fresh.plug_charge["session"] is None
    asyncio.run(run())


def test_manual_release_after_a_refusal_releases_this_connection_by_hand():
    async def run():
        h = Harness()
        c = await armed(h)
        # Switched off by hand, this connection is refused even when recognized.
        await c.async_set_charging_release(False)
        present(h)
        with pytest.raises(DeviceValidationError) as exc:
            await authorize(c)
        assert exc.value.translation_key == "plug_charge_blocked"
        await c.async_set_charging_release(True)
        assert c.charging_release_requested is True
        assert c.plug_charge["manual"]
        assert not c.plug_charge["authorized"]
        assert h.transport.bank[R.CHARGING_RELEASE.address] == 1
        await c.async_shutdown()
    asyncio.run(run())


def test_a_refused_manual_release_does_not_stay_armed():
    async def run():
        h = Harness()
        c = await armed(h)
        h.transport.bank[R.LOCK_EVSE.address] = 1
        c.queue.async_submit_locked = AsyncMock(side_effect=DeviceValidationError("refused"))
        with pytest.raises(DeviceValidationError):
            await c.async_set_charging_release(True)
        assert not c.plug_charge["manual"]
        await c.async_shutdown()
    asyncio.run(run())


def test_manual_release_request_does_not_clear_independent_goal_stop():
    async def run():
        h = Harness()
        c = await armed(h)
        c.charging_release_requested = False
        c.energy_goal._status = "reached"
        before_generation = c.write_state.command_intent_generation
        with pytest.raises(DeviceValidationError) as exc:
            await c.async_set_charging_release(True)
        assert exc.value.translation_key == "energy_goal_stop_active"
        assert c.charging_release_requested is False
        assert c.write_state.command_intent_generation == before_generation
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await c.async_shutdown()
    asyncio.run(run())


def test_manual_gate_release_keeps_a_waiting_lock_but_ends_goal_recovery():
    async def run():
        h = Harness(state_change_interval_seconds=300)
        c = await armed(h)
        await c.async_set_lock_evse(True)
        assert c.pending_action == "lock"
        persist = AsyncMock()
        goal = EnergyGoalController(c, h.client, persist)
        goal.model.resume_on_recovery = True
        # A release does not undo a waiting lock: it is refused, while the
        # deliberate command still ends the goal's automatic recovery.
        with pytest.raises(AmtronRateLimitedError):
            await c.async_set_charging_release(True)
        assert c.pending_action == "lock"
        assert not goal.model.resume_on_recovery
        persist.assert_awaited_once()
        assert h.written(R.LOCK_EVSE) == []
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await goal.async_stop()
        await c.async_shutdown()
    asyncio.run(run())


def test_a_car_seen_again_only_after_the_grace_loses_its_grant_and_gets_a_fresh_one(caplog):
    async def run():
        h = Harness()
        c = await armed(h)
        token = c.plug_charge["session"]
        await authorize(c)
        # One failed poll, and the next one answers only minutes later.
        c.observe_plug(None)
        h.clock[0] += 300
        present(h)
        await c._plug_charge._task
        assert "vehicle state unknown for over 60 seconds" in caplog.text
        assert not c.plug_charge["authorized"]
        assert h.written(R.CHARGING_RELEASE) == [[0], [1], [0]]
        assert c.plug_charge["session"] == token
        # A grant on evidence read in its own command gets the whole grace.
        c.observe_plug(None)
        h.clock[0] += 59
        await authorize(c)
        h.clock[0] += 59
        c.observe_plug(None)
        assert c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_unknown_vehicle_state_keeps_the_grant_until_the_grace_ends(caplog):
    async def run():
        h = Harness()
        c = await armed(h)
        token = c.plug_charge["session"]
        await authorize(c)
        # A failed poll or a transient EVSE/CP combination is no unplug.
        c.observe_plug(None)
        h.clock[0] += 30
        c.observe_plug(WallboxData(values={
            "evse_state": int(EvseState.IDLE), "cp_state": int(CpState.B1), "charging_release": 1,
        }))
        assert c.plug_charge["authorized"]
        present(h)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        # A longer gap withdraws the grant but keeps the connection and its start.
        h.clock[0] += 70
        c.observe_plug(None)
        # A held status is no fresh evidence either; the poll that answers
        # closes the release.
        h.clock[0] += 61
        c.observe_plug(WallboxData(
            values={"evse_state": int(EvseState.EV_CONNECTED), "cp_state": int(CpState.B1), "charging_release": 1},
            held=frozenset({"evse_state", "cp_state"}),
        ))
        await c._plug_charge._task
        assert "vehicle state unknown for over 60 seconds" in caplog.text
        assert not c.plug_charge["authorized"]
        assert h.written(R.CHARGING_RELEASE) == [[0], [1], [0]]
        present(h)
        assert c.plug_charge["session"] == token
        # The same connection is granted again without replugging.
        await authorize(c)
        assert c.plug_charge["authorized"]
        unplugged(c)
        assert "Plug & Charge: grant ended, vehicle unplugged" in caplog.text
        assert c.plug_charge["session"] is None
        await c.async_shutdown()

    import logging
    caplog.set_level(logging.INFO)
    asyncio.run(run())


def test_a_reconnect_only_suspects_and_a_closed_client_forgets():
    write_state = WriteState()
    state, cycle = write_state.plug_charge, write_state.plug_cycle
    state.configure(True, POLICY)
    cycle.suspect(now=0)
    see(state, cycle, None, now=100)
    assert not cycle.lapsed  # Nothing was connected: nothing to doubt.
    see(state, cycle, True, now=100)
    state.authorized = True
    token = cycle.id
    # The doubt counts from the reconnect, and only once.
    cycle.suspect(now=100)
    cycle.suspect(now=150)
    see(state, cycle, None, now=159)
    assert state.authorized
    see(state, cycle, True, now=159)
    see(state, cycle, None, now=300)
    assert state.authorized
    cycle.suspect(now=300)
    see(state, cycle, None, now=360)
    assert cycle.lapsed
    assert not state.authorized
    assert cycle.id == token
    # A closed or new client may reach another connection: nothing is left.
    see(state, cycle, True, now=361)
    state.authorized, state.manual = True, True
    write_state.forget_connection()
    assert (cycle.id, cycle.present, state.authorized, state.manual) == (None, None, False, False)
    # A release given for the next plug-in was no connection's and stays.
    state.manual = True
    write_state.forget_connection()
    assert state.manual


def test_only_a_lasting_unknown_state_withdraws_the_grant_and_changes_the_scope():
    state, cycle = PlugChargeState(), PlugCycle()
    state.configure(True, POLICY)
    see(state, cycle, True, now=0)
    state.authorized = True
    scope = cycle.scope
    see(state, cycle, None, now=5)
    see(state, cycle, None, now=64)
    assert state.authorized
    assert cycle.scope == scope
    see(state, cycle, None, now=65)
    assert not state.authorized
    # Nobody can tell whether the same car is still there.
    assert cycle.scope is None
    see(state, cycle, True, now=70)
    assert cycle.scope not in (None, scope)
    # A release by hand given meanwhile was for a car nobody saw.
    see(state, cycle, None, now=80)
    see(state, cycle, None, now=141)
    state.manual = True
    see(state, cycle, None, now=142)
    assert not state.manual


def test_a_release_whose_car_left_is_saved_as_none():
    state = PlugChargeState()
    state.configure(True, POLICY)
    # In memory the mark of the connection outlives the release it marked.
    state.manual, state.manual_connected = False, True
    saved = state.durable()
    assert (saved["manual"], saved["manual_connected"]) == (False, False)
    assert not PlugChargeState.restore(saved).restored_manual
    with pytest.raises(ValueError, match="invalid saved Plug & Charge"):
        PlugChargeState.restore({**saved, "manual": True, "manual_connected": 1})


def test_a_release_by_hand_knows_at_once_whether_a_car_is_connected(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        gate = service.control._plug_charge
        await service.control.async_configure_plug_charge(True, policy=POLICY)
        state = gate.state
        gate.cycle.observe(True)
        gate.set_manual(True)
        # Saved by the command itself, before the next poll looks.
        assert state.durable()["manual_connected"] is True
        gate.set_manual(False)
        gate.cycle.observe(False)
        gate.set_manual(True)
        assert state.durable()["manual_connected"] is False
        await service.stop()

    asyncio.run(run())


def test_a_refused_release_keeps_what_was_known_about_the_restored_one(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        control = service.control
        await control.async_configure_plug_charge(True, policy=POLICY)
        state = control._plug_charge.state
        # As read from disk: a connected car's release, the car still unknown.
        cycle = control.write_state.plug_cycle
        cycle.forget()
        state.manual = state.manual_connected = state.restored_manual = True

        async def refuse(_command):
            raise DeviceError("write_rate_limited")

        control.queue.async_submit_locked = refuse
        with pytest.raises(DeviceError):
            await control.async_set_charging_release(True)
        assert state.manual_marks(cycle) == (True, True, True, cycle.stamp)
        # The first answer shows no car: the release was that car's and ends.
        see(state, cycle, False)
        assert not state.manual
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("released", [False, True])
def test_a_refused_release_does_not_bring_back_one_that_an_unplug_ended(tmp_path, released):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        control = service.control
        serial = service.identity.serial_number
        await control.async_configure_plug_charge(True, policy=POLICY)
        transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        transport.bank[R.CP_STATE.address] = int(CpState.B1)
        await service.refresh()
        state = control._plug_charge.state
        clock[0] += 1000
        await service.command({"command": "release", "value": True, "expected_serial_number": serial})
        assert state.manual
        clock[0] += 1000
        real = control._client.async_write_register

        async def lost(spec, value, **kwargs):
            if spec == R.CHARGING_RELEASE:
                # The car is pulled and a poll sees it while the command
                # waits for the bus; then the command's write is lost.
                transport.bank[R.EVSE_STATE.address] = int(EvseState.IDLE)
                transport.bank[R.CP_STATE.address] = int(CpState.A1)
                await service._poll()
                raise AmtronConnectionError("timeout")
            return await real(spec, value, **kwargs)

        control._client.async_write_register = lost
        if released:
            transport.bank[R.CHARGING_RELEASE.address] = 0
        with pytest.raises((DeviceError, AmtronConnectionError)):
            await service.command({"command": "release", "value": released, "expected_serial_number": serial})
        control._client.async_write_register = real
        assert not state.manual
        transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
        transport.bank[R.CP_STATE.address] = int(CpState.B1)
        await service.refresh()
        # The next car, any car, is not released by the one that left.
        assert state.blocked
        await service.stop()

    asyncio.run(run())


def test_a_refused_release_for_the_next_car_is_gone_when_that_car_came_meanwhile(tmp_path):
    async def run():
        service, transport, clock = await service_fixture(tmp_path)
        await service.heartbeat.async_stop()
        control = service.control
        serial = service.identity.serial_number
        await control.async_configure_plug_charge(True, policy=POLICY)
        await service.refresh()
        state = control._plug_charge.state
        assert control.write_state.plug_cycle.id is None
        assert not state.manual
        clock[0] += 1000
        real = control._client.async_write_register

        async def lost(spec, value, **kwargs):
            if spec == R.CHARGING_RELEASE:
                # A car is plugged in and a poll sees it while the command
                # waits for the bus; then the command's write is lost.
                transport.bank[R.EVSE_STATE.address] = int(EvseState.EV_CONNECTED)
                transport.bank[R.CP_STATE.address] = int(CpState.B1)
                await service._poll()
                raise AmtronConnectionError("timeout")
            return await real(spec, value, **kwargs)

        control._client.async_write_register = lost
        with pytest.raises((DeviceError, AmtronConnectionError)):
            await service.command({"command": "release", "value": True, "expected_serial_number": serial})
        control._client.async_write_register = real
        # The command failed: it released nothing, also not this car.
        assert control.write_state.plug_cycle.id is not None
        assert not state.manual
        assert state.blocked
        await service.stop()

    asyncio.run(run())


def test_a_failing_read_back_does_not_close_an_acknowledged_grant():
    async def run():
        h = Harness()
        control = await armed(h)
        control._request_refresh = AsyncMock(side_effect=OSError("offline"))
        await authorize(control)
        # The grant itself reads nothing back; the command does, afterwards.
        control._request_refresh.assert_not_awaited()
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        assert control.plug_charge["authorized"]
        await control.async_shutdown()
    asyncio.run(run())


def test_a_refused_command_cannot_put_back_a_release_that_lapsed_meanwhile():
    state, cycle = PlugChargeState(), PlugCycle()
    state.configure(True, POLICY)
    see(state, cycle, True, now=0)
    state.manual = state.manual_connected = True
    # A second command takes the marks and waits; the state stays unknown.
    marks = state.manual_marks(cycle)
    see(state, cycle, None, now=10)
    see(state, cycle, None, now=70)
    assert not state.manual
    # Refused afterwards, it restores nothing: the connection is no longer
    # surely the one the release was given for.
    state.restore_manual(marks, cycle)
    assert not state.manual
    see(state, cycle, True, now=80)
    state.restore_manual(marks, cycle)
    assert not state.manual
