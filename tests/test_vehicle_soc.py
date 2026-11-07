"""Vehicle inputs keep actual stop evidence and never manufacture a charge."""

from __future__ import annotations

import asyncio
from copy import copy
from itertools import product
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.mennekes_amtron_rtu.device.errors import DeviceError
from custom_components.mennekes_amtron_rtu.device.persistence import (
    StorageCommitUncertainError,
)
from custom_components.mennekes_amtron_rtu.device.soc_goal import SocGoal
from custom_components.mennekes_amtron_rtu.vehicle_soc import (
    VehicleSocObserver,
    _Observation,
    _Observations,
    vehicle_percentage,
)


class Coordinator:
    def __init__(self):
        self.last_update_success = True
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def update(self, available=True):
        self.last_update_success = available
        for listener in tuple(self.listeners):
            listener()


class Control:
    def __init__(self):
        self.supports_soc_goal = True
        self.soc_goal = SocGoal()
        self.async_configure_soc = AsyncMock(side_effect=self.configure)
        self.async_observe_soc = AsyncMock(side_effect=self.observe)
        self.async_set_soc_goal = AsyncMock(side_effect=self.set_goal)

    async def configure(self, source):
        self.soc_goal.configure_source(source)

    async def observe(self, soc, maximum, *, source):
        assert source == self.soc_goal.source
        self.soc_goal.observe(soc, maximum)

    async def set_goal(self, value, *, source, soc, maximum):
        assert source == self.soc_goal.source
        self.soc_goal.set_target(value, soc, maximum)


@pytest.mark.parametrize(("value", "attributes", "limit", "expected"), [
    ("0", {}, False, 0), ("80", {"unit_of_measurement": "%"}, False, 80),
    ("80 %", {}, True, 80), ("100", {}, True, 100),
    ("0", {}, True, None), ("-1", {}, False, None), ("101", {}, False, None),
    ("nan", {}, False, None), ("inf", {}, False, None), ("true", {}, False, None),
    ("unknown", {}, False, None), ("unavailable", {}, False, None),
    ("80", {"restored": True}, False, None),
    ("80", {"unit_of_measurement": "kWh"}, False, None),
])
def test_percentage_inputs_are_actual_valid_vehicle_data(value, attributes, limit, expected):
    assert vehicle_percentage(State("sensor.car", value, attributes), limit=limit) == expected
    assert vehicle_percentage(None, limit=limit) is None


@pytest.mark.parametrize("status", ["disabled", "active", "reached"])
def test_bounded_folding_preserves_model_semantics_for_ordered_inputs(status):
    initial = SocGoal()
    initial.configure_source("sensor.car|number.limit")
    initial.set_target(80 if status != "disabled" else 0, 90 if status == "reached" else 10, 100)
    values = (
        _Observation(None, None), _Observation(95, 100), _Observation(50, 40),
        _Observation(None, 80), _Observation(10, 100), _Observation(75, None),
    )
    for sequence in product(values, repeat=4):
        expected = copy(initial)
        folded = _Observations(initial, initial.source)
        for observation in sequence:
            expected.observe(observation.soc, observation.maximum)
            folded.add(observation)
        actual = copy(initial)
        for observation in folded.outgoing():
            actual.observe(observation.soc, observation.maximum)
        assert actual.snapshot() == expected.snapshot(), sequence
        assert len(folded.outgoing()) <= 2


def test_observer_uses_real_ha_events_and_removes_its_listeners(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "50", {"unit_of_measurement": "%"})
        hass.states.async_set("number.car_limit", "80", {"unit_of_measurement": "%"})
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car", "number.car_limit")
        try:
            await observer.async_start()
            assert observer.source == "sensor.car|number.car_limit"
            assert observer.latest_soc == 50
            assert observer.maximum == 80
            await observer.async_set_target(75)
            assert control.soc_goal.status == "active"
            # Attribute-only and unchanged reports must not flood the service.
            initial = control.async_observe_soc.await_count
            hass.states.async_set("sensor.car", "50", {"unit_of_measurement": "%"})
            await hass.async_block_till_done()
            assert control.async_observe_soc.await_count == initial
            hass.states.async_set("sensor.car", "unavailable")
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            assert observer.latest_soc is None
            hass.states.async_set("sensor.car", "75")
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            hass.states.async_set("sensor.car", "20")
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            await observer.async_set_target(0)
            assert control.soc_goal.status == "disabled"
            await observer.async_stop()
            count = control.async_observe_soc.await_count
            hass.states.async_set("sensor.car", "80")
            await hass.async_block_till_done()
            assert control.async_observe_soc.await_count == count
            assert coordinator.listeners == []
            with pytest.raises(ServiceValidationError):
                await observer.async_set_target(80)
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_new_target_preserves_transient_threshold_while_command_is_in_flight(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "50")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
        started, release = asyncio.Event(), asyncio.Event()

        async def slow_set(value, *, source, soc, maximum):
            started.set()
            await release.wait()
            await control.set_goal(value, source=source, soc=soc, maximum=maximum)

        try:
            await observer.async_start()
            control.async_set_soc_goal.side_effect = slow_set
            setting = asyncio.create_task(observer.async_set_target(80))
            await started.wait()
            hass.states.async_set("sensor.car", "85")
            hass.states.async_set("sensor.car", "50")
            # Dispatch callbacks, leaving the observation worker waiting on
            # the command lock until the target request finishes.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            await setting
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
        finally:
            release.set()
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_failed_target_keeps_threshold_evidence_for_previous_goal(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "50")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
        started, release = asyncio.Event(), asyncio.Event()

        async def failing_set(value, *, source, soc, maximum):
            started.set()
            await release.wait()
            raise DeviceError("service_unavailable")

        try:
            await observer.async_start()
            await observer.async_set_target(70)
            control.async_set_soc_goal.side_effect = failing_set
            setting = asyncio.create_task(observer.async_set_target(90))
            await started.wait()
            hass.states.async_set("sensor.car", "75")
            hass.states.async_set("sensor.car", "50")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(DeviceError):
                await setting
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 70
            assert control.soc_goal.status == "reached"
        finally:
            release.set()
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_uncertain_committed_target_keeps_its_transient_threshold(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "50")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
        started, release = asyncio.Event(), asyncio.Event()

        async def uncertain_set(value, *, source, soc, maximum):
            await control.set_goal(value, source=source, soc=soc, maximum=maximum)
            started.set()
            await release.wait()
            raise StorageCommitUncertainError("uncertain")

        try:
            await observer.async_start()
            control.async_set_soc_goal.side_effect = uncertain_set
            setting = asyncio.create_task(observer.async_set_target(80))
            await started.wait()
            hass.states.async_set("sensor.car", "85")
            hass.states.async_set("sensor.car", "50")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(StorageCommitUncertainError):
                await setting
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
        finally:
            release.set()
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_configured_but_unknown_cap_prevents_arming_until_known(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control = Control()
        observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car", "number.limit")
        try:
            await observer.async_start()
            with pytest.raises(ServiceValidationError) as err:
                await observer.async_set_target(80)
            assert err.value.translation_key == "soc_goal_limit_unavailable"
            await observer.async_set_target(0)
            hass.states.async_set("number.limit", "80")
            await hass.async_block_till_done()
            hass.states.async_set("number.limit", "unavailable")
            await hass.async_block_till_done()
            await observer.async_set_target(80)
            assert control.soc_goal.target_percent == 80
            assert control.soc_goal.maximum_percent == 80
            assert observer.maximum is None
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_absent_source_clears_an_old_goal_and_old_service_is_optional(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        control.soc_goal.configure_source("sensor.old|")
        control.soc_goal.set_target(80)
        control.supports_soc_goal = False
        observer = VehicleSocObserver(hass, control, coordinator, None)
        try:
            await observer.async_start()
            control.async_configure_soc.assert_not_awaited()
            control.supports_soc_goal = True
            coordinator.update()
            await hass.async_block_till_done()
            control.async_configure_soc.assert_awaited_once_with(None)
            control.async_observe_soc.assert_not_awaited()
            assert control.soc_goal.status == "disabled"
            assert control.soc_goal.source is None
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_source_binding_failure_unsubscribes_and_propagates(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        control.async_configure_soc.side_effect = DeviceError("service_unavailable")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
        with pytest.raises(DeviceError):
            await observer.async_start()
        assert coordinator.listeners == []
        assert observer._stopping
        await hass.async_stop()

    asyncio.run(run())


def test_retry_retains_threshold_evidence_and_current_cap_after_failure(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "10")
        hass.states.async_set("number.limit", "100")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car", "number.limit")
        try:
            await observer.async_start()
            await observer.async_set_target(80)
            control.async_observe_soc.side_effect = DeviceError("service_unavailable")
            hass.states.async_set("sensor.car", "85")
            await hass.async_block_till_done()
            hass.states.async_set("sensor.car", "20")
            hass.states.async_set("number.limit", "60")
            await hass.async_block_till_done()
            hass.states.async_set("number.limit", "90")
            await hass.async_block_till_done()
            assert control.soc_goal.status == "active"
            control.async_observe_soc.side_effect = control.observe
            coordinator.update(False)
            coordinator.update(True)
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            assert control.soc_goal.target_percent == 60
            assert control.soc_goal.maximum_percent == 90
            assert observer._pending is None
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_pending_observations_account_for_an_inflight_cap_reduction(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control = Control()
        hass.states.async_set("sensor.car", "10")
        hass.states.async_set("number.limit", "100")
        observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car", "number.limit")
        try:
            await observer.async_start()
            await observer.async_set_target(80)
            started, release = asyncio.Event(), asyncio.Event()

            async def blocked(soc, maximum, *, source):
                started.set()
                await release.wait()
                await control.observe(soc, maximum, source=source)

            control.async_observe_soc.side_effect = blocked
            hass.states.async_set("number.limit", "40")
            await asyncio.wait_for(started.wait(), 1)
            hass.states.async_set("sensor.car", "45")
            await asyncio.sleep(0)
            hass.states.async_set("number.limit", "90")
            hass.states.async_set("sensor.car", "10")
            await asyncio.sleep(0)
            release.set()
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            assert control.soc_goal.target_percent == 40
            assert control.soc_goal.maximum_percent == 90
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_lower_cap_after_missing_soc_does_not_reuse_earlier_soc(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control = Control()
        hass.states.async_set("sensor.car", "10")
        hass.states.async_set("number.limit", "100")
        observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car", "number.limit")
        try:
            await observer.async_start()
            await observer.async_set_target(100)
            async with observer._command_lock:
                hass.states.async_set("sensor.car", "95")
                await asyncio.sleep(0)
                hass.states.async_set("sensor.car", "unavailable")
                hass.states.async_set("number.limit", "80")
                await asyncio.sleep(0)
            await hass.async_block_till_done()
            assert control.soc_goal.target_percent == 80
            assert control.soc_goal.status == "active"
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())


def test_nested_state_updates_cannot_hide_a_reached_target(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control = Control()
        hass.states.async_set("sensor.car", "10")

        @callback
        def overwrite(event):
            if event.data["new_state"].state == "90":
                hass.states.async_set("sensor.car", "20")

        remove = async_track_state_change_event(hass, "sensor.car", overwrite)
        observer = VehicleSocObserver(hass, control, Coordinator(), "sensor.car")
        try:
            await observer.async_start()
            await observer.async_set_target(80)
            hass.states.async_set("sensor.car", "90")
            await hass.async_block_till_done()
            assert observer.latest_soc == 20
            assert control.soc_goal.status == "reached"
        finally:
            remove()
            await observer.async_stop()
            observer._queue_current()  # Already dispatched callbacks may outlive removal.
            await hass.async_stop()

    asyncio.run(run())


def test_failed_target_keeps_unsent_evidence_and_unload_cancels_the_worker(tmp_path):
    async def run():
        hass = HomeAssistant(str(tmp_path))
        control, coordinator = Control(), Coordinator()
        hass.states.async_set("sensor.car", "10")
        observer = VehicleSocObserver(hass, control, coordinator, "sensor.car")
        try:
            await observer.async_start()
            await observer.async_set_target(80)
            started, release = asyncio.Event(), asyncio.Event()

            async def fail_after_new_data(*_args, **_kwargs):
                started.set()
                await release.wait()
                raise DeviceError("service_unavailable")

            control.async_observe_soc.side_effect = fail_after_new_data
            hass.states.async_set("sensor.car", "90")
            await asyncio.wait_for(started.wait(), 1)
            hass.states.async_set("sensor.car", "20")
            release.set()
            await hass.async_block_till_done()
            control.async_set_soc_goal.side_effect = DeviceError("service_unavailable")
            with pytest.raises(DeviceError):
                await observer.async_set_target(100)
            await hass.async_block_till_done()
            assert observer._pending.model.stop_requested
            # The same pending evidence retries when the service is reachable.
            control.async_observe_soc.side_effect = control.observe
            coordinator.update()
            await hass.async_block_till_done()
            assert control.soc_goal.status == "reached"
            release.clear()
            started.clear()
            control.async_observe_soc.side_effect = fail_after_new_data
            hass.states.async_set("sensor.car", "30")
            await asyncio.wait_for(started.wait(), 1)
            task = observer._task
            await observer.async_stop()
            assert task.cancelled()
            assert observer._task is None
        finally:
            await observer.async_stop()
            await hass.async_stop()

    asyncio.run(run())
