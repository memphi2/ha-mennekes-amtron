"""The bus owner grants only a current plug cycle and retains independent stops."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.control import AmtronControl, ControlOptions
from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.energy_goal_controller import (
    EnergyGoalController,
)
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from custom_components.mennekes_amtron_rtu.device.plug_charge import PlugChargeState
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.test_control import Harness
from tests.test_modbus_service import service_fixture

POLICY = "a" * 64


def present(harness, connected=True):
    values = {
        "evse_state": int(EvseState.EV_CONNECTED if connected else EvseState.IDLE),
        "cp_state": int(CpState.B1 if connected else CpState.A1),
        "charging_release": harness.transport.bank[R.CHARGING_RELEASE.address],
    }
    for spec in (R.EVSE_STATE, R.CP_STATE):
        harness.transport.bank[spec.address] = values[spec.key]
    harness.control.observe_plug_charge(WallboxData(values=values))


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


def test_cycle_and_policy_bindings_have_no_durable_grants():
    state = PlugChargeState()
    state.configure(True, POLICY)
    before_absence = datetime.now(UTC)
    state.observe(False, observed_at=before_absence)
    absent_at = state.last_absent_at
    state.observe(True)
    token = state.session
    assert len(token) == 32
    assert before_absence == state.since == absent_at
    state.authorized, state.hold = True, False
    state.observe(True)
    state.configure(True, POLICY)
    assert state.session == token
    assert state.authorized
    restored = PlugChargeState.restore(state.durable())
    assert restored.enabled
    assert restored.blocked
    assert restored.session is None
    assert restored.since is None
    state.configure(True, "b" * 64)
    assert state.session is None
    assert state.last_absent_at is None
    assert state.blocked
    state.observe(True)
    assert state.session != token
    state.observe(None)
    assert state.session is None
    assert state.last_absent_at is None
    state.configure(False, "")
    assert state.blocked
    assert not state.enabled
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
        await control.async_set_charging_release(True)
        assert control.charging_release_requested is True
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert control.events.snapshot()["events"] == []
        await authorize(control)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        assert control.plug_charge["authorized"]
        assert h.written(R.CHARGING_CURRENT_EMS) == []
        assert h.written(R.LOCK_EVSE) == []
        assert control.charging_release_requested is True
        await control.async_configure_plug_charge(True, policy=POLICY)
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        present(h)
        await asyncio.sleep(0)
        assert control.plug_charge["authorized"]
        await control.async_shutdown()
    asyncio.run(run())


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
        with pytest.raises(ServiceValidationError):
            await authorize(c)
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert not c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_read_only_cannot_enable_gate():
    async def run():
        h = Harness(read_only=True)
        c = await h.connect()
        with pytest.raises(ServiceValidationError) as exc:
            await c.async_configure_plug_charge(True, policy=POLICY)
        assert exc.value.translation_key == "read_only_mode"
        assert not c.plug_charge["enabled"]
        assert h.transport.writes == []
    asyncio.run(run())


@pytest.mark.parametrize("lost", ["unplug", "unknown", "transport", "policy"])
def test_lost_physical_cycle_rejects_old_authorization(lost):
    async def run():
        h = Harness()
        c = await armed(h)
        token = c.plug_charge["session"]
        await authorize(c)
        if lost == "unplug":
            present(h, False)
        elif lost == "unknown":
            c.observe_plug_charge(None)
        elif lost == "transport":
            h.client._connection_changed(False)
            c.observe_plug_charge(None)
        else:
            await c.async_configure_plug_charge(True, policy="b" * 64)
        await c.async_sync_plug_charge()
        present(h)
        with pytest.raises(ServiceValidationError) as exc:
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
        with pytest.raises(ServiceValidationError) as exc:
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
        with pytest.raises(ServiceValidationError):
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
                        c.observe_plug_charge(None)
                    elif race == "goal":
                        c.energy_goal._status = "reached"
                    else:
                        await asyncio.sleep(0.04)
            return result
        monkeypatch.setattr(h.transport, "read_holding_registers", read)
        with pytest.raises(ServiceValidationError):
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
            assert snap["supports_plug_charge"]
            assert snap["plug_charge"]["session"]
            saved = json.loads((tmp_path / "intent.json").read_text())["plug_charge"]
            assert saved == {"enabled": True, "policy": POLICY, "hold": True}
            restored = WriteState()
            IntentStore(tmp_path / "intent.json", service.identity.serial_number).load(restored)
            assert restored.plug_charge.enabled
            assert restored.plug_charge.blocked
            assert restored.plug_charge.session is None
            assert restored.plug_charge.since is None
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
        assert c.plug_charge["session"] is None
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
        with pytest.raises(ServiceValidationError) as exc:
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
                c.observe_plug_charge(None)
            return result
        monkeypatch.setattr(h.transport, "read_holding_registers", read)
        with pytest.raises(ServiceValidationError) as exc:
            await c.async_set_charging_release(True)
        assert exc.value.translation_key == "plug_charge_blocked"
        assert h.written(R.CHARGING_RELEASE) == [[0], [1]]
        await c.async_shutdown()
    asyncio.run(run())


def test_late_deny_task_does_not_close_a_newly_authorized_cycle():
    async def run():
        h = Harness()
        c = await armed(h)
        c.observe_plug_charge(None)  # A deny worker exists but has not run yet.
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
            assert set(stored) == {"enabled", "policy", "hold"}
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
            c.observe_plug_charge(None)
            await c._plug_charge._task
        assert caplog.text.count("Cannot enforce Plug & Charge release gate") == 1
        h.transport.write_exceptions.clear()
        c.observe_plug_charge(None)
        await c._plug_charge._task
        assert h.transport.bank[R.CHARGING_RELEASE.address] == 0
        await c.async_shutdown()
        snapshot = c.plug_charge
        present(h)
        assert c.plug_charge == snapshot
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
        with pytest.raises(ServiceValidationError):
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
        with pytest.raises(ServiceValidationError) as exc:
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
        old.observe_plug_charge(WallboxData(values={"evse_state": int(EvseState.IDLE), "cp_state": int(CpState.A1), "charging_release": 0}), observed_at=absent_at)
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
        if link_lost:
            assert fresh.plug_charge["session"] != prior["session"]
            assert datetime.fromisoformat(fresh.plug_charge["since"]) > absent_at
            with pytest.raises(ServiceValidationError) as exc:
                await fresh.async_authorize_plug_charge(prior["session"], deadline=deadline())
            assert exc.value.translation_key == "plug_charge_session_changed"
        else:
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


def test_manual_release_request_clears_previous_refusal_without_bypassing_vehicle_gate():
    async def run():
        h = Harness()
        c = await armed(h)
        await c.async_set_charging_release(False)
        present(h, False)
        present(h)
        with pytest.raises(ServiceValidationError) as exc:
            await authorize(c)
        assert exc.value.translation_key == "plug_charge_blocked"
        before_writes = list(h.transport.writes)
        before_events = c.events.snapshot()
        before_generation = c.write_state.command_intent_generation
        before_refreshes = h.refreshes
        await c.async_set_charging_release(True)
        assert c.charging_release_requested is True
        assert c.write_state.command_intent_generation == before_generation + 1
        assert h.transport.writes == before_writes
        assert c.events.snapshot() == before_events
        assert h.refreshes == before_refreshes + 1
        assert not c.plug_charge["authorized"]
        assert h.transport.bank[R.CHARGING_RELEASE.address] == 0
        await authorize(c)
        assert h.transport.bank[R.CHARGING_RELEASE.address] == 1
        assert c.plug_charge["authorized"]
        await c.async_shutdown()
    asyncio.run(run())


def test_manual_release_request_does_not_clear_independent_goal_stop():
    async def run():
        h = Harness()
        c = await armed(h)
        c.charging_release_requested = False
        c.energy_goal._status = "reached"
        before_generation = c.write_state.command_intent_generation
        with pytest.raises(ServiceValidationError) as exc:
            await c.async_set_charging_release(True)
        assert exc.value.translation_key == "energy_goal_stop_active"
        assert c.charging_release_requested is False
        assert c.write_state.command_intent_generation == before_generation
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        await c.async_shutdown()
    asyncio.run(run())


def test_manual_gate_intent_replaces_old_pending_command_and_goal_recovery():
    async def run():
        h = Harness(state_change_interval_seconds=300)
        c = await armed(h)
        await c.async_set_lock_evse(True)
        assert c.pending_action == "lock"
        persist = AsyncMock()
        goal = EnergyGoalController(c, h.client, persist)
        goal.model.resume_on_recovery = True
        await c.async_set_charging_release(True)
        assert c.pending_action is None
        assert not goal.model.resume_on_recovery
        persist.assert_awaited_once()
        assert h.written(R.LOCK_EVSE) == []
        assert h.written(R.CHARGING_RELEASE) == [[0]]
        assert c.events.snapshot()["events"][-1]["outcome"] == "replaced"
        await goal.async_stop()
        await c.async_shutdown()
    asyncio.run(run())
