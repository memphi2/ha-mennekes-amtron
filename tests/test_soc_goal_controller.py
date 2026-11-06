"""Vehicle percentage limits share the same stop fence as energy ceilings."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.mennekes_amtron_rtu.device.errors import (
    DeviceValidationError,
    validation_error,
)
from custom_components.mennekes_amtron_rtu.device.service_store import IntentStore
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.device.soc_goal_controller import (
    SocGoalController,
)
from custom_components.mennekes_amtron_rtu.device.write_state import WriteState
from tests.test_energy_goal_controller import _runner, _settle, _snapshot
from tests.test_modbus_service import current_writes, service_fixture

SOURCE = "sensor.car_soc|number.car_limit"


async def _soc_runner(*, read_only=False, persist=None, notify=None):
    harness, control, energy, saved = await _runner(read_only=read_only)
    soc = SocGoalController(control, persist or AsyncMock(), notify, sleep=harness._sleep)
    await control.async_configure_soc(SOURCE)
    return harness, control, energy, soc, saved


@pytest.mark.parametrize("read_only", [False, True])
def test_configuration_and_missing_soc_never_write_or_resume(read_only):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner(read_only=read_only)
        await control.async_observe_soc(None, 80, source=SOURCE)
        assert control.soc_goal.maximum_percent == 80
        assert control.soc_goal.target_percent == 0
        assert harness.transport.writes == []
        if read_only:
            with pytest.raises(ServiceValidationError):
                await control.async_set_soc_goal(70, source=SOURCE, soc=20)
        else:
            await control.async_set_soc_goal(70, source=SOURCE, soc=20)
            await control.async_observe_soc(None, None, source=SOURCE)
            assert control.soc_goal.status == "active"
            assert harness.transport.writes == []
        await control.async_configure_soc(None)
        assert control.soc_goal.source is None
        assert control.soc_goal.target_percent == 0
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_reached_soc_persists_and_pauses_without_long_mode_wait():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20, maximum=90)
        control.write_state.mode_change_at = harness.clock[0]
        await control.async_observe_soc(80, source=SOURCE)
        assert control.energy_goal_stop_requested
        await _settle(soc)
        assert current_writes(harness.transport) == [1]
        assert control.soc_goal.status == "reached"
        await control.async_observe_soc(None, None, source=SOURCE)
        await _settle(soc)
        assert control.soc_goal.status == "reached"
        with pytest.raises(ServiceValidationError):
            await control.async_resume_charging()
        await control.async_set_soc_goal(0, source=SOURCE)
        assert current_writes(harness.transport) == [1]
        assert control.pause_requested is True
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_cap_reduction_can_reach_goal_and_old_source_is_rejected():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20, maximum=90)
        await control.async_observe_soc(65, 60, source=SOURCE)
        await _settle(soc)
        assert control.soc_goal.target_percent == 60
        assert current_writes(harness.transport) == [1]
        await control.async_configure_soc("sensor.other|")
        assert control.soc_goal.status == "disabled"
        with pytest.raises(ServiceValidationError):
            await control.async_observe_soc(100, source=SOURCE)
        with pytest.raises(ServiceValidationError):
            await control.async_set_soc_goal(20, source=SOURCE)
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_soc_evidence_fences_a_resume_already_waiting_on_the_lock():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20)
        async with control.energy_goal_guard:
            start = asyncio.create_task(control.async_resume_charging())
            await asyncio.sleep(0)
            observation = asyncio.create_task(control.async_observe_soc(80, source=SOURCE))
            await asyncio.sleep(0)
        with pytest.raises(ServiceValidationError):
            await start
        await observation
        await _settle(soc)
        assert current_writes(harness.transport) == [1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_source_change_discards_old_observation_waiting_on_the_lock():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20)
        async with control.energy_goal_guard:
            change = asyncio.create_task(control.async_configure_soc("sensor.new|"))
            await asyncio.sleep(0)
            sample = asyncio.create_task(control.async_observe_soc(90, source=SOURCE))
            await asyncio.sleep(0)
        await change
        await sample
        assert control.soc_goal.source == "sensor.new|"
        assert control.soc_goal.status == "disabled"
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_soc_stop_overrides_energy_outage_recovery():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20)
        await energy.async_set_target(12)
        energy.observe(None)
        await _settle(energy)
        await control.async_observe_soc(80, source=SOURCE)
        await _settle(soc)
        energy.observe(_snapshot(harness, 1235))
        await _settle(energy)
        assert current_writes(harness.transport) == [1]
        assert control.soc_goal.stop_requested
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_persistence_failure_keeps_old_goal_but_observed_stop_still_pauses():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20)
        soc._persist = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError, match="disk full"):
            await control.async_set_soc_goal(0, source=SOURCE)
        assert control.soc_goal.target_percent == 80
        with pytest.raises(OSError, match="disk full"):
            await control.async_observe_soc(80, source=SOURCE)
        await _settle(soc)
        assert control.soc_goal.stop_requested
        assert current_writes(harness.transport) == [1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_soc_restore_and_service_commands_survive_owner_restart(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path)
        serial = service.identity.serial_number
        async def command(name, **payload):
            return await service.command({"command": name, "expected_serial_number": serial, **payload})
        try:
            await command("soc_source", source=SOURCE)
            await command("soc_goal", source=SOURCE, value=80, soc=20, maximum=90)
            snapshot = await command("soc_observation", source=SOURCE, soc=80)
            await _settle(service.soc_goal)
            assert snapshot["control"]["soc_goal"]["status"] == "reached"
            assert snapshot["control"]["supports_soc_goal"]
            assert current_writes(transport) == [1]
            state = WriteState()
            IntentStore(tmp_path / "intent.json", serial).load(state)
            assert state.soc_goal.stop_requested
            assert SocGoal.restore(state.soc_goal.snapshot()).source == SOURCE
        finally:
            await service.stop()
    asyncio.run(run())


def test_service_readonly_metadata_works_while_bus_is_unavailable(tmp_path):
    async def run():
        service, transport, _clock = await service_fixture(tmp_path, read_only=True)
        serial = service.identity.serial_number
        service._success = False
        try:
            await service.command({"command": "soc_source", "expected_serial_number": serial, "source": SOURCE})
            await service.command({"command": "soc_observation", "expected_serial_number": serial, "source": SOURCE, "soc": 80})
            assert service.control.soc_goal.source == SOURCE
            assert transport.writes == []
            with pytest.raises(ValueError, match="source"):
                await service.command({"command": "soc_observation", "expected_serial_number": serial, "soc": 80})
        finally:
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize(("old_target", "new_target", "sample", "expected"), [(80, 90, 95, "reached"), (90, 70, 75, "active")])
def test_failed_target_replacement_retains_new_samples_for_original_target(old_target, new_target, sample, expected):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(old_target, source=SOURCE, soc=20)
        saving = asyncio.Event()
        release = asyncio.Event()
        async def fail_once():
            saving.set()
            await release.wait()
            soc._persist = AsyncMock()
            raise OSError("disk full")
        soc._persist = fail_once
        setting = asyncio.create_task(control.async_set_soc_goal(new_target, source=SOURCE, soc=20))
        await saving.wait()
        observation = asyncio.create_task(control.async_observe_soc(sample, source=SOURCE))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(OSError, match="disk full"):
            await setting
        await observation
        await _settle(soc)
        assert control.soc_goal.target_percent == old_target
        assert control.soc_goal.status == expected
        assert current_writes(harness.transport) == ([1] if expected == "reached" else [])
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_failed_cap_persistence_is_retried_even_if_next_sample_is_identical():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await control.async_set_soc_goal(80, source=SOURCE, soc=20, maximum=90)
        soc._persist = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError, match="disk full"):
            await control.async_observe_soc(20, 70, source=SOURCE)
        await _settle(soc)
        assert soc._dirty
        soc._persist = AsyncMock()
        await control.async_observe_soc(20, 70, source=SOURCE)
        assert soc._persist.await_count == 1
        assert not soc._dirty
        assert control.soc_goal.target_percent == 70
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["source", "target", "stopped"])
def test_invalid_or_stopped_configuration_leaves_previous_goal(operation):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        previous = control.soc_goal.snapshot()
        if operation == "source":
            request = soc.async_configure_source("")
        elif operation == "target":
            request = soc.async_set_target(101, source=SOURCE)
        else:
            await soc.async_stop()
            request = soc.async_set_target(80, source=SOURCE)
        with pytest.raises(DeviceValidationError):
            await request
        await soc.async_configure_source(SOURCE)
        if operation == "stopped":
            await soc.async_observe(100, source=SOURCE)
        assert control.soc_goal.snapshot() == previous
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("with_energy", [False, True])
def test_cancelled_committed_save_retains_new_goal(with_energy):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        if not with_energy:
            control.goal_controller = None
        await soc.async_configure_source("sensor.changed|")
        async def committed_then_cancelled():
            raise asyncio.CancelledError
        soc._persist = committed_then_cancelled
        with pytest.raises(asyncio.CancelledError):
            await soc.async_set_target(80, source="sensor.changed|", soc=20)
        assert control.soc_goal.target_percent == 80
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_failed_source_change_does_not_apply_the_new_cars_soc_to_old_goal():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        saving, release = asyncio.Event(), asyncio.Event()
        async def fail():
            saving.set()
            await release.wait()
            raise OSError("disk full")
        soc._persist = fail
        setting = asyncio.create_task(soc.async_configure_source("sensor.new|"))
        await saving.wait()
        sample = asyncio.create_task(soc.async_observe(95, 70, source="sensor.new|"))
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(OSError, match="disk full"):
            await setting
        await sample
        assert control.soc_goal.source == SOURCE
        assert control.soc_goal.target_percent == 80
        assert control.soc_goal.status == "active"
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_concurrent_samples_preserve_reached_evidence_then_coalesce():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        async with control.energy_goal_guard:
            first = asyncio.create_task(soc.async_observe(90, source=SOURCE))
            await asyncio.sleep(0)
            second = asyncio.create_task(soc.async_observe(20, source=SOURCE))
            await asyncio.sleep(0)
        await asyncio.gather(first, second)
        await _settle(soc)
        assert control.soc_goal.stop_requested
        assert current_writes(harness.transport) == [1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_five_second_floor_and_failing_ui_do_not_prevent_goal_pause():
    async def run():
        def broken_ui():
            raise RuntimeError("observer failed")
        harness, control, energy, soc, _ = await _soc_runner(notify=broken_ui)
        await soc.async_set_target(80, source=SOURCE, soc=20)
        await control.async_set_charging_current(12)
        await soc.async_observe(80, source=SOURCE)
        await _settle(soc)
        assert harness.slept == [5]
        assert current_writes(harness.transport) == [12, 1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_bus_failure_is_bounded_and_later_poll_retries_pause(caplog):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        original_pause = control.async_pause_for_energy_goal
        control.async_pause_for_energy_goal = AsyncMock(side_effect=validation_error("read_only_mode"))
        await soc.async_observe(80, source=SOURCE)
        await _settle(soc)
        soc.retry_stop()
        await _settle(soc)
        assert caplog.text.count("Cannot enforce percentage goal") == 1
        control.async_pause_for_energy_goal = original_pause
        soc.retry_stop()
        await _settle(soc)
        assert current_writes(harness.transport) == [1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


@pytest.mark.parametrize("storage_fails", [False, True])
def test_shutdown_persists_observed_goal_after_cancelled_observation(storage_fails):
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        async with control.energy_goal_guard:
            observation = asyncio.create_task(soc.async_observe(80, source=SOURCE))
            await asyncio.sleep(0)
            observation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observation
            assert soc.stop_requested
        if storage_fails:
            soc._persist = AsyncMock(side_effect=OSError("disk full"))
        else:
            soc._persist = AsyncMock()
        await soc.async_stop()
        assert control.soc_goal.stop_requested
        assert soc._persist.await_count == 1
        assert harness.transport.writes == []
        await energy.async_stop()
    asyncio.run(run())


def test_shutdown_cancels_a_pause_waiting_for_the_five_second_floor():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        waiting = asyncio.Event()
        async def blocked(_delay):
            waiting.set()
            await asyncio.Event().wait()
        soc._sleep = blocked
        await soc.async_set_target(80, source=SOURCE, soc=20)
        await control.async_set_charging_current(12)
        await soc.async_observe(80, source=SOURCE)
        await waiting.wait()
        await soc.async_stop()
        assert current_writes(harness.transport) == [12]
        assert control.soc_goal.stop_requested
        await energy.async_stop()
    asyncio.run(run())


def test_failed_reconfiguration_preserves_an_existing_unsaved_cap():
    async def run():
        _harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20, maximum=90)
        soc._persist = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError, match="disk full"):
            await soc.async_observe(20, 70, source=SOURCE)
        await _settle(soc)
        with pytest.raises(OSError, match="disk full"):
            await soc.async_set_target(60, source=SOURCE, soc=20)
        await _settle(soc)
        assert soc._dirty
        soc._persist = AsyncMock()
        await soc.async_observe(20, 70, source=SOURCE)
        assert soc._persist.await_count == 1
        assert not soc._dirty
        assert control.soc_goal.target_percent == 70
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_later_poll_enforces_evidence_from_a_cancelled_observation():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        async with control.energy_goal_guard:
            observation = asyncio.create_task(soc.async_observe(80, source=SOURCE))
            await asyncio.sleep(0)
            observation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observation
        soc.retry_stop()
        await _settle(soc)
        assert control.soc_goal.stop_requested
        assert current_writes(harness.transport) == [1]
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())


def test_explicit_disable_before_the_stop_worker_runs_does_not_pause():
    async def run():
        harness, control, energy, soc, _ = await _soc_runner()
        await soc.async_set_target(80, source=SOURCE, soc=20)
        await soc.async_observe(80, source=SOURCE)
        await soc.async_set_target(0, source=SOURCE)
        await _settle(soc)
        assert control.soc_goal.status == "disabled"
        assert harness.transport.writes == []
        await soc.async_stop()
        await energy.async_stop()
    asyncio.run(run())
