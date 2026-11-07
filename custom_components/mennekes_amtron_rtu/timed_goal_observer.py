"""Plan one vehicle charge from HA data without extra Modbus reads."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .bavarian_departure import read_bavarian_departure
from .charging_plan import ChargePlan, plan_current, usable_capacity
from .device.client_errors import AmtronBusError
from .device.enums import DetectedEvPhases, GridPhasesConnected, SwitchedPhases
from .device.registers import CP_STATE, EVSE_STATE
from .device.vehicle import vehicle_connected
from .exceptions import service_validation_error
from .goal_store import TimedGoalStore
from .timed_goal import TimedGoal
from .timed_goal_const import (
    CONF_TIMED_BAVARIAN_DEVICE,
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_VEHICLE_TIME_ZONE,
)
from .vehicle_soc import vehicle_percentage
from .vehicle_soc_const import CONF_VEHICLE_SOC_ENTITY

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

_LOGGER = logging.getLogger(__name__)
_MIN_CHANGE_A = 1.0
_MIN_WRITE_INTERVAL = 300.0
_PAUSE_MARGIN = timedelta(seconds=360)


def departure_time(state: State | None) -> datetime | None:
    """Accept only an actual dated instant, never a bare vehicle clock time."""
    if state is None or state.attributes.get("restored") is True:
        return None
    if state.entity_id.startswith("input_datetime."):
        if state.attributes.get("has_date") is False or state.attributes.get("has_time") is False:
            return None
        stamp = state.attributes.get("timestamp")
        if not isinstance(stamp, bool) and isinstance(stamp, (int, float)) and math.isfinite(stamp):
            try:
                return datetime.fromtimestamp(stamp, UTC)
            except (OverflowError, ValueError):
                return None
        return None
    try:
        value = datetime.fromisoformat(state.state)
    except (OverflowError, ValueError):
        return None
    return value if value.tzinfo is not None and value.utcoffset() is not None else None


def capacity_from_state(state: State | None) -> float | None:
    """Reject restored, unavailable or unitless nominal capacity guesses."""
    if state is None or state.attributes.get("restored") is True:
        return None
    try:
        value = float(state.state)
    except (OverflowError, ValueError):
        return None
    return usable_capacity(value, state.attributes.get("unit_of_measurement", ""))


class TimedGoalObserver:
    """Own one departure and issue only meaningful, bounded current changes."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._runtime = entry.runtime_data
        self._store = TimedGoalStore(hass, entry.entry_id, entry.runtime_data.identity.serial_number or "")
        self.goal = TimedGoal()
        self.status = "disabled"
        self._unsubs: list[CALLBACK_TYPE] = []
        self._task: asyncio.Task[None] | None = None
        self._pending = False
        self._stopping = False
        self._last_write_at: float | None = None
        self._last_error: str | None = None
        self._goal_lock = asyncio.Lock()
        self._command_owner = f"timed:{entry.entry_id}"
        self._command_submitted = False

    async def async_start(self) -> None:
        # The service may retain a queued command after an unreachable unload
        # or HA crash. Reclaim this entry's stable owner before arming a trip.
        self._command_submitted = True
        await self._cancel_command()
        self.goal = await self._store.async_load()
        options = self._entry.options
        sources = [
            options.get(CONF_VEHICLE_SOC_ENTITY),
            options.get(CONF_TIMED_CAPACITY_ENTITY),
            options.get(CONF_TIMED_DEPARTURE_ENTITY),
        ]
        entities = [entity for entity in sources if isinstance(entity, str)]
        if entities:
            self._unsubs.append(async_track_state_change_event(
                self._hass, entities, self._source_changed,
            ))
        self._unsubs.append(async_track_time_interval(
            self._hass, self._timer, timedelta(minutes=1),
        ))
        self._unsubs.append(self._runtime.coordinator.async_add_listener(self.request_evaluate))
        self.request_evaluate()

    async def async_stop(self) -> None:
        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        try:
            await self._cancel_command()
        except (AmtronBusError, HomeAssistantError, OSError) as err:
            # The independent service still expires the queued command at its
            # deadline. A disconnected service must not abort HA teardown.
            _LOGGER.warning(
                "Could not cancel timed charging during unload: %s",
                getattr(err, "translation_key", None) or type(err).__name__,
            )

    @callback
    def _source_changed(self, _event: object) -> None:
        self.request_evaluate()

    @callback
    def _timer(self, _now: datetime) -> None:
        self.request_evaluate()

    @callback
    def request_evaluate(self) -> None:
        if self._stopping:
            return
        self._pending = True
        if self._task is None or self._task.done():
            self._task = self._hass.async_create_task(
                self._run(), "amtron_timed_goal", eager_start=False,
            )

    async def _run(self) -> None:
        while self._pending and not self._stopping:
            self._pending = False
            try:
                await self._evaluate()
            except (AmtronBusError, HomeAssistantError) as err:
                self._set_status("write_pending")
                message = str(err)
                if message != self._last_error:
                    _LOGGER.warning("Timed charging command deferred: %s", message)
                    self._last_error = message
            except Exception:
                _LOGGER.exception("Timed charging evaluation failed")
                self._set_status("error")

    async def async_set_deadline(
        self, value: datetime | None, *, source: str | None = None,
    ) -> None:
        await self._set_deadline(value, source=source)

    async def _set_deadline(
        self, value: datetime | None, *, source: str | None,
        expected: TimedGoal | None = None,
    ) -> None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise HomeAssistantError("Departure time needs a timezone")
            if value <= dt_util.utcnow():
                raise HomeAssistantError("Departure time must be in the future")
        async with self._goal_lock:
            if self._stopping or (expected is not None and self.goal is not expected):
                return
            original = self.goal
            if source is None:
                # An explicit selection/clear wins over the current external
                # timestamp until that source changes to another trip.
                source_entity = self._entry.options.get(CONF_TIMED_DEPARTURE_ENTITY)
                source_time = departure_time(self._hass.states.get(source_entity)) if source_entity else None
                source = (
                    f"entity:{source_entity}:{source_time.isoformat()}"
                    if source_time is not None else original.source
                )
            candidate = TimedGoal.restore(original.snapshot())
            try:
                changed = candidate.set(value, source)
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            if not changed:
                return
            await self._cancel_command()
            await self._store.async_save(candidate)
            self.goal = candidate
        self._notify()
        self.request_evaluate()

    async def _finish(
        self, reason: str, expected: TimedGoal, *, clear_deadline: bool = False,
    ) -> None:
        async with self._goal_lock:
            if self.goal is not expected or self._stopping:
                return
            candidate = TimedGoal.restore(expected.snapshot())
            candidate.finish(reason, clear_deadline=clear_deadline)
            await self._cancel_command()
            await self._store.async_save(candidate)
            self.goal = candidate
        self._set_status(reason)
        self._notify()

    async def async_use_next_calendar_event(self) -> None:
        """Select a single matching future event; later events never roll in."""
        entity_id = self._entry.options.get(CONF_TIMED_CALENDAR_ENTITY)
        title = self._entry.options.get(CONF_TIMED_CALENDAR_TITLE)
        if not isinstance(entity_id, str) or not isinstance(title, str) or not title:
            raise HomeAssistantError("A calendar and exact event title are required")
        now = dt_util.utcnow()
        end = now + timedelta(days=14)
        original = self.goal
        response = await self._hass.services.async_call(
            "calendar", "get_events",
            {"entity_id": entity_id, "start_date_time": now.isoformat(),
             "end_date_time": end.isoformat()},
            blocking=True, return_response=True,
        )
        payload = response.get(entity_id) if isinstance(response, dict) else None
        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            raise HomeAssistantError("Calendar did not return a valid event list")
        starts: list[datetime] = []
        for event in events:
            if not isinstance(event, dict) or event.get("summary") != title:
                continue
            raw = event.get("start")
            if not isinstance(raw, str) or "T" not in raw:
                continue
            try:
                start = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if start.tzinfo is not None and start.utcoffset() is not None and now < start <= end:
                starts.append(start)
        if not starts:
            raise HomeAssistantError("No matching timed calendar event in the next 14 days")
        await self._set_deadline(
            min(starts), source=None, expected=original,
        )

    async def async_use_bmw_departure(self) -> None:
        """Copy one enabled local BMW timer; never arm a recurring schedule."""
        options = self._entry.options
        original = self.goal
        value = read_bavarian_departure(
            self._hass, options.get(CONF_TIMED_BAVARIAN_DEVICE),
            now=dt_util.utcnow(),
            time_zone=options.get(CONF_TIMED_VEHICLE_TIME_ZONE, self._hass.config.time_zone),
            timer1_days=options.get(CONF_TIMED_TIMER1_DAYS, ()),
            timer2_days=options.get(CONF_TIMED_TIMER2_DAYS, ()),
        )
        if value is None:
            raise service_validation_error("timed_bavarian_unavailable")
        await self._set_deadline(value, source=None, expected=original)

    def _capacity(self) -> float | None:
        options = self._entry.options
        entity = options.get(CONF_TIMED_CAPACITY_ENTITY)
        if entity:
            return capacity_from_state(self._hass.states.get(entity))
        return usable_capacity(options.get(CONF_TIMED_CAPACITY_KWH))

    def _phases(self) -> int:
        data = self._runtime.coordinator.current_data()
        switched = data.get("switched_phases")
        connected = data.get("grid_phases_connected")
        if switched == SwitchedPhases.SINGLE_PHASE or connected == GridPhasesConnected.L1:
            return 1
        if switched == SwitchedPhases.ALL_AVAILABLE and connected == GridPhasesConnected.L1_L2_L3:
            detected = data.get("detected_ev_phases")
            if detected in (DetectedEvPhases.ONE_PHASE, DetectedEvPhases.TWO_PHASES):
                return int(detected)
            return 3
        # Unknown phase switching cannot justify a three-phase estimate.
        return 1

    async def _evaluate(self) -> None:
        options = self._entry.options
        source_entity = options.get(CONF_TIMED_DEPARTURE_ENTITY)
        if source_entity:
            value = departure_time(self._hass.states.get(source_entity))
            if value is not None and value > dt_util.utcnow():
                source = f"entity:{source_entity}:{value.isoformat()}"
                if source != self.goal.source:
                    await self._set_deadline(value, source=source, expected=self.goal)
        trip = self.goal
        deadline = trip.deadline
        if deadline is None:
            self._set_status(trip.finished or "disabled")
            return
        if deadline <= dt_util.utcnow():
            await self._finish(trip.finished or "late", trip, clear_deadline=True)
            return
        if trip.finished is not None:
            self._set_status(trip.finished)
            return
        control = self._runtime.control
        goal = control.soc_goal
        if goal.status == "reached":
            await self._finish("reached", trip)
            return
        if goal.status != "active" or goal.target_percent <= 0:
            await self._wait_for_input("waiting_for_target")
            return
        capacity = self._capacity()
        if capacity is None:
            await self._wait_for_input("waiting_for_capacity")
            return
        soc_entity = options.get(CONF_VEHICLE_SOC_ENTITY)
        soc = vehicle_percentage(
            self._hass.states.get(soc_entity) if isinstance(soc_entity, str) else None
        )
        if soc is None:
            await self._wait_for_input("waiting_for_soc")
            return
        if not self._runtime.coordinator.last_update_success:
            await self._wait_for_input("waiting_for_device")
            return
        if control.read_only:
            await self._wait_for_input("read_only")
            return
        data = self._runtime.coordinator.current_data()
        if vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=self._runtime.identity.layout_version >= CP_STATE.min_layout,
        ) is not True:
            await self._wait_for_input("waiting_for_device")
            return
        plan: ChargePlan = plan_current(
            now=dt_util.utcnow(), deadline=deadline,
            soc_percent=soc, target_percent=goal.target_percent,
            capacity_kwh=capacity, phases=self._phases(),
            max_current_a=control.max_charging_current,
        )
        if plan.status == "reached" or control.energy_goal.status == "reached":
            await self._finish("reached", trip)
            return
        if control.energy_goal_stop_requested:
            # Missing/reset meter data and stop evidence awaiting persistence
            # block commands without proving that either target was reached.
            await self._wait_for_input("waiting_for_device")
            return
        if control.pending_action is not None:
            self._set_status("queued")
            return
        if plan.start_at is not None and plan.start_at - dt_util.utcnow() > _PAUSE_MARGIN:
            self._set_status("paused_until_start")
            if control.is_paused is False:
                await self._write(None, trip)
            return
        self._set_status(plan.status)
        if plan.current_a is None or control.is_paused is None:
            return
        current = self._runtime.coordinator.current_data().get("charging_current_ems")
        if control.is_paused or not isinstance(current, (int, float)) or (
            current == 0 or abs(float(current) - plan.current_a) >= _MIN_CHANGE_A
        ):
            await self._write(plan.current_a, trip)

    async def _write(self, current: float | None, expected: TimedGoal) -> None:
        async with self._goal_lock:
            if self.goal is not expected or self._stopping:
                return
            await self._write_current(current, expected.deadline)

    async def _write_current(self, current: float | None, deadline: datetime | None) -> None:
        if deadline is None or deadline <= dt_util.utcnow():
            return
        now = time.monotonic()
        if self._last_write_at is not None and now - self._last_write_at < _MIN_WRITE_INTERVAL:
            return
        # Record even uncertain writes, so reconnect churn cannot flood the bus.
        self._last_write_at = now
        self._command_submitted = True
        await self._runtime.control.async_set_timed_current(
            current, deadline=deadline, owner=self._command_owner,
        )
        self._last_error = None

    async def _cancel_command(self) -> None:
        if self._command_submitted:
            await self._runtime.control.async_cancel_timed_current(self._command_owner)
            self._command_submitted = False

    async def _wait_for_input(self, status: str) -> None:
        # Keep an already applied charge unchanged, but do not let an older
        # queued start run after the plan's target or input became unavailable.
        await self._cancel_command()
        self._set_status(status)

    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self._notify()

    def _notify(self) -> None:
        self._runtime.coordinator.async_update_listeners()
