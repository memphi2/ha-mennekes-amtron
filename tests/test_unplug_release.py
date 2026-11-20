"""The service closes the charging release after an unplug, in one place."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronRateLimitedError,
)
from custom_components.mennekes_amtron_rtu.device.control_types import PendingCommand
from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.unplug_release import (
    UnplugReleaseState,
)
from tests.fakes import put
from tests.test_modbus_service import service_fixture


def _plug(transport, connected: bool, *, release: int | None = None) -> None:
    transport.bank[R.EVSE_STATE.address] = 2 if connected else 1
    transport.bank[R.CP_STATE.address] = 11 if connected else 10
    if release is not None:
        transport.bank[R.CHARGING_RELEASE.address] = release


def _release_writes(transport) -> list[int]:
    return [words[0] for address, words in transport.writes if address == R.CHARGING_RELEASE.address]


async def _command(service, command: str, **values):
    return await service.command({
        "command": command, "expected_serial_number": service.identity.serial_number, **values,
    })


async def _rule(tmp_path, *, enabled: bool = True, **kwargs):
    """A service that has seen a car plugged in with the release on."""
    service, transport, clock = await service_fixture(tmp_path, **kwargs)
    await service.heartbeat.async_stop()
    if enabled:
        await _command(service, "unplug_release_config", value=True)
    _plug(transport, True, release=1)
    await service.refresh()
    transport.writes.clear()
    return service, transport, clock


async def _poll(service) -> None:
    await service.refresh()
    await service._close_release_after_unplug()


def test_the_state_owes_only_a_confirmed_unplug_of_an_enabled_rule():
    state = UnplugReleaseState()
    state.observe(True)
    state.observe(False)
    assert not state.owed
    state.configure(True)
    # Never seen plugged in since: nothing to close.
    state.observe(False)
    assert not state.owed
    state.observe(True)
    state.observe(None)
    assert (state.plugged, state.owed) == (True, False)
    state.observe(False)
    assert state.owed
    # The car is back before it was done: its release is left alone.
    state.observe(True)
    assert not state.owed
    state.observe(False)
    state.configure(False)
    assert not state.owed
    with pytest.raises(ValueError, match="invalid unplug rule"):
        state.configure("yes")


@pytest.mark.parametrize("raw", [
    None, {}, {"enabled": True, "plugged": None, "owed": False},
    {"enabled": 1, "plugged": None, "owed": False, "goal_owed": None},
    {"enabled": True, "plugged": "yes", "owed": False, "goal_owed": None},
    {"enabled": True, "plugged": None, "owed": 0, "goal_owed": None},
    {"enabled": True, "plugged": None, "owed": False, "goal_owed": True},
    {"enabled": True, "plugged": None, "owed": False, "goal_owed": "other"},
])
def test_an_invalid_saved_rule_is_rejected(raw):
    with pytest.raises(ValueError, match="invalid saved unplug rule"):
        UnplugReleaseState.restore(raw)


def test_a_saved_rule_comes_back_and_owes_nothing_once_disabled():
    state = UnplugReleaseState(True, True, True)
    assert UnplugReleaseState.restore(state.snapshot()) == state
    assert not UnplugReleaseState.restore(
        {"enabled": False, "plugged": False, "owed": True, "goal_owed": None},
    ).owed
    owing = UnplugReleaseState(False, False, False, "goal")
    assert UnplugReleaseState.restore(owing.snapshot()) == owing


def test_only_an_unplug_with_a_target_owes_its_end():
    state = UnplugReleaseState()
    state.observe(True)
    state.observe(False)
    assert state.goal_owed is None
    # A target set without a car is the next car's.
    state.observe(False, armed=True)
    assert state.goal_owed is None
    state.observe(True, armed=True)
    state.observe(None, armed=True)
    state.observe(False, armed=True)
    assert state.goal_owed == "goal"
    state.observe(True, armed=True)
    assert state.goal_owed is None


@pytest.mark.parametrize("rule", [False, True])
def test_an_unplug_ends_a_reached_target_and_its_pause(tmp_path, rule):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=rule)
        control = service.control
        await _command(service, "energy_goal", value=1)
        put(transport.bank, R.ENERGY_TOTAL, control.energy_goal.baseline_kwh + 2)
        clock[0] += 1000
        service._poller._slow_read_at = None
        await service.refresh()
        if control.goal_controller._task is not None:
            await control.goal_controller._task
        assert (control.energy_goal.status, control.pause_requested) == ("reached", True)
        # Still plugged in: the reached target stays, as does its pause.
        await _poll(service)
        assert control.energy_goal.status == "reached"
        clock[0] += 1000
        _plug(transport, False)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed is None
        assert (control.energy_goal.status, control.energy_goal.target_kwh) == ("disabled", 0)
        # The next car starts without the previous one's limit or pause.
        assert control.pause_requested is False
        assert (_release_writes(transport) == [0]) is rule
        await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize(("paused", "resumed"), [(False, False), (True, True)])
def test_an_unplug_switches_the_release_off_at_once(tmp_path, paused, resumed):
    async def run():
        service, transport, clock = await _rule(tmp_path)
        control = service.control
        if paused:
            await control.async_pause_charging()
            # Past the floor between two current writes.
            clock[0] += 10
            transport.writes.clear()
        _plug(transport, False)
        await _poll(service)
        assert _release_writes(transport) == [0]
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        # The intent follows: a recovery must not open it again.
        assert control.charging_release_requested is False
        assert not control.write_state.unplug_release.owed
        # Written directly: nothing waits in a queue for a car to return to.
        assert control.pending_action is None
        event = control.events.snapshot()["events"][-1 if not resumed else -2]
        assert (event["action"], event["source"]) == ("disable_charging", "unplug")
        # With the release off, ending the pause lets no current flow.
        assert (control.pause_requested is True) is (paused and not resumed)
        # Done once: later polls write nothing.
        transport.writes.clear()
        await _poll(service)
        assert _release_writes(transport) == []
        await service.stop()

    asyncio.run(run())


def test_the_rule_is_off_by_default_and_takes_its_setting_without_the_wallbox(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path, enabled=False)
        assert service.snapshot()["control"]["release_off_on_unplug"] is False
        _plug(transport, False)
        await _poll(service)
        assert _release_writes(transport) == []
        # A setting only: accepted while the wallbox does not answer.
        service._success = False
        snapshot = await _command(service, "unplug_release_config", value=True)
        assert snapshot["control"]["release_off_on_unplug"] is True
        with pytest.raises(Exception, match=r"invalid_request|invalid unplug rule"):
            await _command(service, "unplug_release_config", value="on")
        await service.stop()

    asyncio.run(run())


def test_a_car_that_is_back_keeps_its_release(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        state = service.control.write_state.unplug_release
        _plug(transport, False)
        await service.refresh()
        assert state.owed
        # Plugged in again before the release was written: the live check
        # refuses, and the next poll drops what was owed.
        _plug(transport, True)
        await service._close_release_after_unplug()
        assert _release_writes(transport) == []
        assert state.owed
        await _poll(service)
        assert not state.owed
        assert _release_writes(transport) == []
        assert transport.bank[R.CHARGING_RELEASE.address] == 1
        await service.stop()

    asyncio.run(run())


def test_a_failed_poll_or_an_unknown_state_is_no_unplug(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        state = service.control.write_state.unplug_release
        transport.bank[R.EVSE_STATE.address] = 0
        await _poll(service)
        assert (state.plugged, state.owed) == (True, False)
        assert _release_writes(transport) == []
        await service.stop()

    asyncio.run(run())


def test_read_only_and_plug_and_charge_leave_the_release_to_others(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        state = service.control.write_state.unplug_release
        _plug(transport, False)
        await service.refresh()
        assert state.owed
        control = type(service.control)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(control, "read_only", property(lambda _self: True))
            await service._close_release_after_unplug()
            assert state.owed
        assert _release_writes(transport) == []
        # The gate owns the release and keeps it closed without a car.
        service.control.write_state.plug_charge.enabled = True
        await service._close_release_after_unplug()
        assert not state.owed
        assert _release_writes(transport) == []
        await service.stop()

    asyncio.run(run())


def test_a_refused_write_is_reported_once_and_retried_later(tmp_path, caplog):
    async def run():
        caplog.set_level(logging.DEBUG)
        service, transport, clock = await _rule(tmp_path)
        state = service.control.write_state.unplug_release
        _plug(transport, False)
        await service.refresh()
        write = transport.write_register

        async def refused(address, value, **kwargs):
            if address == R.CHARGING_RELEASE.address:
                raise AmtronConnectionError("no answer")
            return await write(address, value, **kwargs)

        transport.write_register = refused
        for _ in range(3):
            clock[0] += 31
            await service._close_release_after_unplug()
        assert state.owed
        failed = [r for r in caplog.records if "not switched off after unplugging" in r.getMessage()]
        assert [r.levelno for r in failed] == [logging.WARNING, logging.DEBUG, logging.DEBUG]
        # Not before the wait is over.
        calls = len(failed)
        await service._close_release_after_unplug()
        assert len([r for r in caplog.records if "not switched off" in r.getMessage()]) == calls
        transport.write_register = write
        clock[0] += 31
        await service._close_release_after_unplug()
        assert not state.owed
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        assert "Control: charging release switched off after unplugging" in caplog.text
        await service.stop()

    asyncio.run(run())


def test_an_unplug_during_a_restart_of_the_service_is_still_found(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        await service.save_intent()
        saved = json.loads((tmp_path / "intent.json").read_text())
        assert saved["unplug_release"] == {
            "enabled": True, "plugged": True, "owed": False, "goal_owed": None,
        }
        await service.stop()
        # The car leaves while nothing runs.
        bank = dict(transport.bank)
        restarted, wallbox, _clock = await service_fixture(tmp_path)
        wallbox.bank.update(bank)
        _plug(wallbox, False, release=1)
        await restarted.heartbeat.async_stop()
        assert restarted.control.write_state.unplug_release.plugged is True
        await _poll(restarted)
        assert wallbox.bank[R.CHARGING_RELEASE.address] == 0
        await restarted.stop()

    asyncio.run(run())


def test_an_older_wallbox_without_cp_state_goes_by_its_idle_state(tmp_path, monkeypatch):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        monkeypatch.setattr(service.identity, "layout_version", R.CP_STATE.min_layout - 1)
        transport.bank[R.EVSE_STATE.address] = 1
        await _poll(service)
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        # Its state cannot be read: not known to be away.
        state = service.control.write_state.unplug_release
        state.owed = True
        read = service.client.async_read_register

        async def unreadable(spec):
            if spec == R.EVSE_STATE:
                raise AmtronConnectionError("no answer")
            return await read(spec)

        monkeypatch.setattr(service.client, "async_read_register", unreadable)
        await service._close_release_after_unplug()
        assert state.owed
        await service.stop()

    asyncio.run(run())



def test_a_release_queued_for_the_car_that_left_never_opens_later(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path)
        control = service.control
        # Off and on again inside the state-change interval: the on waits.
        await control.async_set_charging_release(False)
        await control.async_set_charging_release(True)
        assert control.pending_action == "enable_charging"
        transport.writes.clear()
        _plug(transport, False)
        await _poll(service)
        assert control.pending_action is None
        assert control.charging_release_requested is False
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        assert _release_writes(transport)[-1] == 0
        await service.stop()

    asyncio.run(run())


def test_a_wallbox_that_cannot_prove_the_car_is_gone_waits_for_the_interval(tmp_path, monkeypatch):
    async def run():
        service, transport, clock = await _rule(tmp_path)
        control = service.control
        state = control.write_state.unplug_release
        monkeypatch.setattr(service.identity, "layout_version", R.CP_STATE.min_layout - 1)
        control.write_state.mode_change_at = clock[0]
        transport.bank[R.EVSE_STATE.address] = 1
        await _poll(service)
        # No failure and nothing written: only later.
        assert state.owed
        assert _release_writes(transport) == []
        clock[0] += 301
        await service._close_release_after_unplug()
        assert not state.owed
        assert _release_writes(transport) == [0]
        await service.stop()

    asyncio.run(run())


def test_a_pause_that_cannot_be_ended_leaves_the_release_closed_and_nothing_owed(tmp_path, monkeypatch, caplog):
    async def run():
        service, transport, clock = await _rule(tmp_path)
        control = service.control
        await control.async_pause_charging()
        clock[0] += 10

        async def refused(**_kwargs):
            raise DeviceError("write_failed")

        monkeypatch.setattr(control, "async_resume_charging", refused)
        _plug(transport, False)
        await _poll(service)
        assert not control.write_state.unplug_release.owed
        assert transport.bank[R.CHARGING_RELEASE.address] == 0
        assert "Control: pause not ended after unplugging (write_failed)" in caplog.text
        assert "not switched off" not in caplog.text
        await service.stop()

    asyncio.run(run())


def test_a_failure_that_ended_with_the_car_coming_back_is_reported_anew_next_time(tmp_path, caplog):
    async def run():
        caplog.set_level(logging.DEBUG)
        service, transport, _clock = await _rule(tmp_path)
        write = transport.write_register

        async def refused(address, value, **kwargs):
            if address == R.CHARGING_RELEASE.address:
                raise AmtronConnectionError("no answer")
            return await write(address, value, **kwargs)

        transport.write_register = refused
        for _ in range(2):
            _plug(transport, False)
            await _poll(service)
            # The car is back: nothing owed, and no wait left over.
            _plug(transport, True)
            await _poll(service)
        failed = [r.levelno for r in caplog.records if "not switched off after unplugging" in r.getMessage()]
        assert failed == [logging.WARNING, logging.WARNING]
        await service.stop()

    asyncio.run(run())


async def _settled(service):
    for _ in range(5):
        if service.control.goal_controller._task is not None:
            await service.control.goal_controller._task
        await asyncio.sleep(0)


async def _reached(service, transport, clock, *, poll=True):
    control = service.control
    await _command(service, "energy_goal", value=1)
    put(transport.bank, R.ENERGY_TOTAL, control.energy_goal.baseline_kwh + 2)
    clock[0] += 1000
    service._poller._slow_read_at = None
    if poll:
        await service.refresh()
        await _settled(service)
        assert (control.energy_goal.status, control.pause_requested) == ("reached", True)
    return control


def test_a_target_first_seen_reached_after_the_unplug_is_ended_too(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        # The meter passes the target and the car leaves before it is read.
        control = await _reached(service, transport, clock, poll=False)
        _plug(transport, False)
        for _ in range(3):
            await _poll(service)
            await _settled(service)
            clock[0] += 1000
        assert control.write_state.unplug_release.goal_owed is None
        assert (control.energy_goal.status, control.pause_requested) == ("disabled", False)
        await service.stop()

    asyncio.run(run())


def test_a_target_not_reached_waits_for_the_next_car(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path, enabled=False)
        control = service.control
        await _command(service, "energy_goal", value=1)
        _plug(transport, False)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed == "goal"
        assert control.energy_goal.status == "active"
        _plug(transport, True, release=1)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed is None
        assert control.energy_goal.status == "active"
        await service.stop()

    asyncio.run(run())


def test_the_pause_stays_owed_until_it_really_ended(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        # Inside the floor between two current writes the resume only queues.
        clock[0] += 2
        _plug(transport, False)
        await _poll(service)
        assert (control.energy_goal.status, state.goal_owed, control.pause_requested) == ("disabled", "pause", True)
        # A restart here loses the queue, not what is owed.
        assert UnplugReleaseState.restore(state.snapshot()).goal_owed == "pause"
        if control._flush_task is not None:
            control._flush_task.cancel()
        control._pending = None
        clock[0] += 1000
        await _poll(service)
        assert (state.goal_owed, control.pause_requested) == (None, False)
        await service.stop()

    asyncio.run(run())


def test_another_limits_pause_is_left_and_ended_once_that_limit_goes(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        control.soc_goal.configure_source("car")
        control.soc_goal.set_target(60, 70, 90)
        clock[0] += 1000
        _plug(transport, False)
        await _poll(service)
        assert (control.energy_goal.status, state.goal_owed, control.pause_requested) == ("disabled", "pause", True)
        # Waiting is no failure: nothing was tried against the other limit.
        assert not control.unplug_release._goal_notice.failing
        control.soc_goal.set_target(0)
        clock[0] += 1000
        await _poll(service)
        assert (state.goal_owed, control.pause_requested) == (None, False)
        await service.stop()

    asyncio.run(run())


def test_a_target_cleared_by_hand_leaves_nothing_owed(tmp_path):
    async def run():
        service, transport, _clock = await _rule(tmp_path, enabled=False)
        control = service.control
        await _command(service, "energy_goal", value=1)
        _plug(transport, False)
        await _poll(service)
        await _command(service, "energy_goal", value=0)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed is None
        await service.stop()

    asyncio.run(run())


def test_a_failed_end_is_retried_and_reported_once(tmp_path, caplog):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        clock[0] += 1000
        _plug(transport, False)
        real = control.async_set_energy_goal

        async def refuse(_value):
            raise OSError("no")

        control.async_set_energy_goal = refuse
        with caplog.at_level("INFO"):
            await _poll(service)
            await _poll(service)
            assert state.goal_owed == "goal"
            assert caplog.text.count("reached target not ended after unplugging") == 1
            control.async_set_energy_goal = real
            # Not before the retry time.
            await _poll(service)
            assert state.goal_owed == "goal"
            clock[0] += 31
            await _poll(service)
        assert (state.goal_owed, control.pause_requested) == (None, False)
        assert "Energy target: reached target ended after unplugging" in caplog.text
        await service.stop()

    asyncio.run(run())


def test_a_car_back_before_the_check_keeps_its_target(tmp_path):
    async def run():
        service, _transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, _transport, clock)
        state = control.write_state.unplug_release
        state.goal_owed = "goal"
        # Owed from an earlier poll, but the wallbox shows a car again.
        await control.unplug_release.async_end_reached_goal()
        assert state.goal_owed == "goal"
        assert (control.energy_goal.status, control.pause_requested) == ("reached", True)
        await service.stop()

    asyncio.run(run())


def test_a_write_that_came_too_soon_waits_for_its_interval(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        clock[0] += 1000
        _plug(transport, False)
        real = control.async_resume_charging
        calls = []

        async def too_soon():
            calls.append(control.monotonic())
            raise AmtronRateLimitedError("charging_current_ems", 4)

        control.async_resume_charging = too_soon
        await _poll(service)
        await _poll(service)
        assert (state.goal_owed, len(calls)) == ("pause", 1)
        control.async_resume_charging = real
        clock[0] += 5
        await _poll(service)
        assert (state.goal_owed, control.pause_requested) == (None, False)
        await service.stop()

    asyncio.run(run())


def test_an_end_that_failed_after_the_target_was_cleared_still_ends_the_pause(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        clock[0] += 1000
        _plug(transport, False)
        real = control.async_set_energy_goal

        async def uncertain(value):
            await real(value)
            raise OSError("directory not synced")

        control.async_set_energy_goal = uncertain
        await _poll(service)
        control.async_set_energy_goal = real
        assert (control.energy_goal.status, control.pause_requested, state.goal_owed) == ("disabled", True, "pause")
        clock[0] += 1000
        await _poll(service)
        assert (control.pause_requested, state.goal_owed) == (False, None)
        assert not control.unplug_release._goal_notice.failing
        await service.stop()

    asyncio.run(run())


def test_a_failed_end_that_the_car_coming_back_settled_is_reported_anew(tmp_path, caplog):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        real = service.control.async_set_energy_goal

        async def refuse(_value):
            raise OSError("no")

        with caplog.at_level("WARNING"):
            for _ in range(2):
                control = await _reached(service, transport, clock)
                clock[0] += 1000
                _plug(transport, False)
                control.async_set_energy_goal = refuse
                await _poll(service)
                control.async_set_energy_goal = real
                _plug(transport, True, release=1)
                await _poll(service)
                assert control.write_state.unplug_release.goal_owed is None
                assert not control.unplug_release._goal_notice.failing
                # The car is back with its target: a new one for the next round.
                await _command(service, "energy_goal", value=0)
                clock[0] += 1000
                await _command(service, "resume")
                clock[0] += 1000
        warnings = [r for r in caplog.records if "not ended after unplugging" in r.getMessage()]
        assert [r.levelname for r in warnings] == ["WARNING", "WARNING"]
        await service.stop()

    asyncio.run(run())


def test_nothing_is_written_read_only_or_without_a_pause(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = await _reached(service, transport, clock)
        state = control.write_state.unplug_release
        state.goal_owed = "goal"
        control.startup_hold = True
        _plug(transport, False)
        await control.unplug_release.async_end_reached_goal()
        assert (state.goal_owed, control.energy_goal.status) == ("goal", "reached")
        control.startup_hold = False
        # The pause's end already waits in the queue: no second one.
        state.goal_owed = "pause"
        control._pending = PendingCommand("resume", 0)
        calls = []
        control.async_resume_charging = lambda: calls.append(1)
        await control.unplug_release.async_end_reached_goal()
        assert (state.goal_owed, calls) == ("pause", [])
        # No pause on: nothing to end, nothing written.
        control._pending = None
        control.pause_requested = False
        await control.unplug_release.async_end_reached_goal()
        assert (state.goal_owed, calls) == (None, [])
        await service.stop()

    asyncio.run(run())


def test_a_pause_set_after_the_unplug_stays_when_the_target_is_cleared_by_hand(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = service.control
        await _command(service, "energy_goal", value=1)
        _plug(transport, False)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed == "goal"
        # For the next car: a pause by hand, then the target taken away.
        clock[0] += 1000
        await _command(service, "pause")
        await _command(service, "energy_goal", value=0)
        await _poll(service)
        assert control.write_state.unplug_release.goal_owed is None
        assert control.pause_requested is True
        await service.stop()

    asyncio.run(run())


def test_a_pause_by_hand_ends_with_the_reached_target_too(tmp_path):
    async def run():
        service, transport, clock = await _rule(tmp_path, enabled=False)
        control = service.control
        await _command(service, "energy_goal", value=1)
        await _command(service, "pause")
        put(transport.bank, R.ENERGY_TOTAL, control.energy_goal.baseline_kwh + 2)
        clock[0] += 1000
        service._poller._slow_read_at = None
        _plug(transport, False)
        for _ in range(3):
            await _poll(service)
            await _settled(service)
            clock[0] += 1000
        # No pause is left behind for the next car, whoever set it.
        assert (control.energy_goal.status, control.pause_requested) == ("disabled", False)
        assert control.write_state.unplug_release.goal_owed is None
        await service.stop()

    asyncio.run(run())
