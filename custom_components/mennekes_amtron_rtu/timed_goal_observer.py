"""Give the service one vehicle's trip and car data; show the plan it makes."""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .device.charging_plan import usable_capacity
from .device.client_errors import AmtronBusError
from .device.departure_plan import DepartureForecast, DepartureInputs, DepartureState
from .device.departure_step import (
    minimum_level,
)
from .device.enums import (
    DetectedEvPhases,
    EvseState,
    GridPhasesConnected,
    SwitchedPhases,
)
from .device.persistence import StorageCommitUncertainError, StorageStateUnknownError
from .device.registers import EVSE_STATE
from .device.timed_goal import TimedGoal, matches_departure_source
from .exceptions import home_assistant_error, service_validation_error
from .goal_store import DepartureIntent, TimedGoalStore
from .timed_goal_const import (
    CONF_TIMED_CALENDAR_ENTITY,
    CONF_TIMED_CALENDAR_TITLE,
    CONF_TIMED_CAPACITY_ENTITY,
    CONF_TIMED_CAPACITY_KWH,
    CONF_TIMED_DEPARTURE_ENTITY,
    CONF_TIMED_MINIMUM_SOC,
)
from .vehicle_phases import VehiclePhaseMemory
from .vehicle_profiles import (
    CONF_TIMED_USE_VEHICLE_CAPACITY,
    CONF_VEHICLE_PROFILE_ID,
    CONF_VEHICLE_SOC_USE_PROFILE,
    vehicle_options,
)
from .vehicle_soc_const import CONF_VEHICLE_SOC_ENTITY
from .vehicle_sources import resolve_departure_source

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

from .device.notice import FailureNotice

_LOGGER = logging.getLogger(__name__)
# A service on another host may finish a trip slightly before this clock does.
_CLOCK_TOLERANCE = timedelta(minutes=5)


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
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(UTC)
    except (OverflowError, ValueError):
        return None
    return None


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
    """Own the selected car's trip and keep the service's plan supplied."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._options = vehicle_options(entry.options)
        self._runtime = entry.runtime_data
        self._store = TimedGoalStore(hass, entry.entry_id, entry.runtime_data.identity.serial_number or "")
        self.goal = TimedGoal()
        self.status = "disabled"
        # When the service's plan starts, or started, charging for the open trip.
        self.planned_start: datetime | None = None
        # The service's forecast for the trip.
        self.forecast: DepartureForecast | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._task: asyncio.Task[None] | None = None
        self._pending = False
        self._stopping = False
        self._handover_notice = FailureNotice(_LOGGER, "Departure plan")
        self._evaluation_notice = FailureNotice(_LOGGER, "Departure plan")
        self._goal_lock = asyncio.Lock()
        self._save_dirty = False
        self._save_unknown = False
        # The selected car's phases from its last charge, if it had one.
        self._known_phases: int | None = None
        # The trip changed here and the service has not taken it over yet.
        self._trip_dirty = False
        self._trip_base: dict[str, Any] | None = None

    async def async_start(self) -> None:
        intent = await self._store.async_load()
        self.goal = intent.trip
        self._trip_dirty = intent.pending
        self._trip_base = {"edit_revision": intent.base_revision} if intent.base_revision is not None else None
        if (profile := self._options.get(CONF_VEHICLE_PROFILE_ID)) is not None:
            self._known_phases = await self._phase_memory().async_phases(profile)
        options = self._options
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
        if self._runtime.vehicle_telemetry is not None and (
            options.get(CONF_TIMED_USE_VEHICLE_CAPACITY) is True
            or options.get(CONF_VEHICLE_SOC_USE_PROFILE) is True
        ):
            self._unsubs.append(self._runtime.vehicle_telemetry.subscribe(self._source_changed))
        self._unsubs.append(async_track_time_interval(
            self._hass, self._timer, timedelta(minutes=1),
        ))
        self._unsubs.append(self._runtime.coordinator.async_add_listener(self.request_evaluate))
        if (binding := getattr(self._runtime, "vehicle_binding", None)) is not None:
            self._unsubs.append(binding.subscribe(self.request_evaluate))
        self.request_evaluate()

    async def async_stop(self, *, withdraw: bool = False) -> None:
        """Stop following the trip; ``withdraw`` also ends this plan's pause.

        A reload keeps the pause, as the same car's plan takes it over again.
        Another car, or none, must not stay paused by this car's plan.
        """
        self._stopping = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        if withdraw:
            # The service plans this car's trip and ends its own pause.
            try:
                await self._runtime.control.async_set_departure_trip(
                    None, self.goal.source, withdraw=True,
                )
            except (AmtronBusError, HomeAssistantError, OSError) as err:
                _LOGGER.warning(
                    "Departure plan: plan for the previous car not withdrawn (%s)",
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
                self._handover_notice.recovered("handed to the service")
                self._evaluation_notice.recovered("evaluation works again")
            except (AmtronBusError, HomeAssistantError) as err:
                self._set_status("write_pending")
                # The cause, not its text: a text that varies would be
                # reported with every evaluation.
                self._handover_notice.failed(
                    "not handed to the service yet",
                    getattr(err, "translation_key", None) or type(err).__name__,
                )
            except Exception as err:  # noqa: BLE001 - one car's plan never stops the others
                self._evaluation_notice.failed(
                    "evaluation failed", type(err).__name__, level=logging.ERROR, exc_info=True,
                )
                self._set_status("error")

    async def async_set_deadline(
        self, value: datetime | None, *, source: str | None = None,
    ) -> None:
        await self._set_user_deadline(value, source=source)

    async def _set_user_deadline(
        self, value: datetime | None, *, source: str | None,
        expected: TimedGoal | None = None,
    ) -> None:
        if self._stopping or (expected is not None and self.goal is not expected):
            return
        if self._runtime.control.guest_active():
            await self._runtime.control.async_set_target_time(value)
            return
        await self._set_deadline(value, source=source, expected=expected)

    async def _set_deadline(
        self, value: datetime | None, *, source: str | None,
        expected: TimedGoal | None = None,
    ) -> None:
        if value is not None and (value.utcoffset() is None or value <= dt_util.utcnow()):
            raise service_validation_error("target_time_not_future")
        async with self._goal_lock:
            if self._stopping or (expected is not None and self.goal is not expected):
                return
            original = self.goal
            if source is None:
                # An explicit selection/clear wins over the current external
                # timestamp until that source changes to another trip.
                source_entity = self._options.get(CONF_TIMED_DEPARTURE_ENTITY)
                source_time = departure_time(self._hass.states.get(source_entity)) if source_entity else None
                source = (
                    f"entity:{source_entity}:{source_time.isoformat()}"
                    if source_time is not None else original.source
                )
            candidate = TimedGoal.restore(original.snapshot())
            # The time was checked above; only a malformed source, which no
            # user input can produce, still fails here.
            changed = candidate.set(value, source)
            if not changed and not self._save_dirty:
                return
            if not self._trip_dirty:
                remote = self._runtime.control.departure
                self._trip_base = remote.selection() if remote is not None else None
            self._trip_dirty = True
            await self._save_goal(candidate)
            # A new trip plans its own start; the old one no longer applies.
            self.planned_start = None
        self._notify()
        self.request_evaluate()

    async def _save_goal(self, candidate: TimedGoal) -> None:
        """Keep the live trip aligned with a save that survives cancellation."""
        intent = DepartureIntent(candidate, self._trip_dirty,
                                 self._trip_base["edit_revision"] if self._trip_dirty and self._trip_base is not None else None)
        task = asyncio.create_task(self._store.async_save(intent))
        try:
            await asyncio.shield(task)
        except (StorageCommitUncertainError, StorageStateUnknownError) as err:
            # The file contains this trip, although directory durability was
            # not confirmed, or its state could not be read. Keep the pending
            # trip and report failure; unknown storage blocks plan commands.
            self._publish_uncertain_goal(candidate, unknown=isinstance(err, StorageStateUnknownError))
            raise
        except asyncio.CancelledError:
            # The store finishes atomic writes even when its caller is
            # cancelled. Shield the whole save, including lock acquisition,
            # so we know whether the candidate was actually committed.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try:
                task.result()
            except (StorageCommitUncertainError, StorageStateUnknownError) as err:
                self._publish_uncertain_goal(candidate, unknown=isinstance(err, StorageStateUnknownError))
                raise
            self.goal = candidate
            self._save_dirty = self._save_unknown = False
            self._notify()
            self.request_evaluate()
            raise
        self.goal = candidate
        self._save_dirty = self._save_unknown = False

    def _publish_uncertain_goal(self, candidate: TimedGoal, *, unknown: bool) -> None:
        self.goal = candidate
        self._save_dirty = True
        self._save_unknown = unknown
        for publish in (self._notify, self.request_evaluate):
            try:
                publish()
            except Exception:  # noqa: BLE001 - preserve the storage failure
                _LOGGER.error("Departure plan: unable to publish an uncertain update")

    async def async_use_next_calendar_event(self) -> None:
        """Select a single matching future event; later events never roll in."""
        entity_id = self._options.get(CONF_TIMED_CALENDAR_ENTITY)
        title = self._options.get(CONF_TIMED_CALENDAR_TITLE)
        if not isinstance(entity_id, str) or not isinstance(title, str) or not title:
            raise service_validation_error("calendar_not_configured")
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
            raise home_assistant_error("calendar_reply_invalid")
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
            raise service_validation_error("calendar_no_event")
        await self._set_user_deadline(
            min(starts), source=None, expected=original,
        )

    async def async_use_vehicle_departure(self) -> None:
        """Copy one enabled local vehicle timer; never arm a recurring schedule."""
        original = self.goal
        source = resolve_departure_source(self._hass, self._options)
        value = source.next_departure(dt_util.utcnow()) if source is not None else None
        if value is None:
            raise service_validation_error("timed_vehicle_unavailable")
        await self._set_user_deadline(value, source=None, expected=original)

    def _capacity(self) -> float | None:
        options = self._options
        if options.get(CONF_TIMED_USE_VEHICLE_CAPACITY) is True:
            telemetry = self._runtime.vehicle_telemetry
            return telemetry.snapshot.capacity_kwh if telemetry is not None else None
        entity = options.get(CONF_TIMED_CAPACITY_ENTITY)
        if entity:
            return capacity_from_state(self._hass.states.get(entity))
        return usable_capacity(options.get(CONF_TIMED_CAPACITY_KWH))

    def _minimum(self) -> float:
        """The level below which the car charges at once; zero is off."""
        return minimum_level(self._options.get(CONF_TIMED_MINIMUM_SOC))

    def _phases(self) -> int:
        data = self._runtime.coordinator.current_data()
        switched = data.get("switched_phases")
        connected = data.get("grid_phases_connected")
        if switched == SwitchedPhases.SINGLE_PHASE or connected == GridPhasesConnected.L1:
            return 1
        if switched == SwitchedPhases.ALL_AVAILABLE and connected == GridPhasesConnected.L1_L2_L3:
            detected = data.get("detected_ev_phases")
            if detected in (
                DetectedEvPhases.ONE_PHASE, DetectedEvPhases.TWO_PHASES,
                DetectedEvPhases.THREE_PHASES,
            ):
                if data.get(EVSE_STATE.key) == EvseState.CHARGING:
                    # Only a drawing car proves the reading is its own.
                    self._remember_phases(int(detected))
                return int(detected)
        # Detection is zero while the car draws no current, i.e. all the time
        # before a late start. The car's last detected phases then decide, and
        # a car never seen charging is assumed three-phase. A one-phase car
        # corrects that on its first charge, from the reserve the start keeps.
        return self._known_phases or 3

    def _remember_phases(self, phases: int) -> None:
        profile = self._options.get(CONF_VEHICLE_PROFILE_ID)
        if profile is None or phases == self._known_phases:
            return
        self._known_phases = phases
        self._hass.async_create_task(
            self._phase_memory().async_remember(profile, phases), "amtron_vehicle_phases",
        )

    def _phase_memory(self) -> VehiclePhaseMemory:
        return VehiclePhaseMemory(
            self._hass, self._entry.entry_id, self._runtime.identity.serial_number or "",
        )

    async def _evaluate(self) -> None:
        if self._save_unknown:
            self._set_status("error")
            return
        source_entity = self._options.get(CONF_TIMED_DEPARTURE_ENTITY)
        if source_entity:
            value = departure_time(self._hass.states.get(source_entity))
            if value is not None and value > dt_util.utcnow():
                source = f"entity:{source_entity}:{value.isoformat()}"
                if not matches_departure_source(self.goal.source, source_entity, value):
                    await self._set_deadline(value, source=source, expected=self.goal)
        await self._follow_service()

    async def _follow_service(self) -> None:
        """Hand trip and car data to the service, which plans; show its state."""
        control = self._runtime.control
        if (remote := control.departure) is not None and not control.guest_active():
            await self._sync_trip(remote)
        if (remote := control.departure) is not None and not control.guest_active():
            inputs = self._departure_inputs(remote.scope)
            if inputs is not None and inputs.digest != remote.inputs:
                await control.async_set_departure_inputs(inputs)
        remote = control.departure
        # The service is still loading its plan.
        status = remote.status if remote is not None else "waiting_for_device"
        start = remote.planned_start if remote is not None else None
        forecast = remote.forecast if remote is not None else None
        if (status, start, forecast) != (self.status, self.planned_start, self.forecast):
            self.status, self.planned_start, self.forecast = status, start, forecast
            self._notify()

    async def _sync_trip(self, remote: DepartureState) -> None:
        service, editor, vehicle = remote.trip, remote.editor, remote.selection_vehicle
        trip = self.goal
        same = (trip.deadline, trip.source) == (service.deadline, service.source)
        selection = remote.selection()
        observer = self._runtime.vehicle_soc
        from_app = editor == "app" and observer is not None and observer.source == vehicle
        if self._trip_dirty and from_app and self._trip_base is not None and selection != self._trip_base:
            # A newer app edit supersedes an unsent HA edit after an outage.
            async with self._goal_lock:
                if self.goal is trip and not self._stopping:
                    self._trip_dirty = False
                    self._trip_base = None
                    await self._save_goal(TimedGoal.restore(service.snapshot()))
                    self._notify()
            return
        if not self._trip_dirty:
            if (trip.deadline is None and service.deadline is None) or (
                same and trip.finished == service.finished
            ):
                return
            # The service decides how a trip ends: it finishes the trip it
            # was given, and clears its time once that has passed.
            ended = (
                service.deadline is None and service.finished is not None
                and trip.deadline is not None
                and trip.deadline <= dt_util.utcnow() + _CLOCK_TOLERANCE
            )
            if same or ended or from_app:
                async with self._goal_lock:
                    if self.goal is trip and not self._stopping:
                        await self._save_goal(TimedGoal.restore(service.snapshot()))
                        self._notify()
                return
        # The trip was set here, or the service lost its plan.
        await self._runtime.control.async_set_departure_trip(
            trip.deadline, trip.source, expected_selection=selection,
        )
        async with self._goal_lock:
            if self.goal is trip and self._trip_dirty:
                self._trip_dirty = False
                self._trip_base = None
                await self._save_goal(trip)

    def _departure_inputs(self, scope: str | None) -> DepartureInputs | None:
        observer = self._runtime.vehicle_soc
        if observer is None or observer.source is None:
            return None
        binding = getattr(self._runtime, "vehicle_binding", None)
        reading = observer.latest_soc_reading
        # Keep learning the car's phases while it charges.
        self._phases()
        return DepartureInputs(
            observer.source,
            # The plug-in this car is confirmed for, as the service names it.
            None if binding is not None and binding.resting else scope,
            self._capacity(), self._known_phases,
            reading[0] if reading is not None else None,
            reading[1] if reading is not None else None,
            self._minimum(),
        )

    def _set_status(self, status: str) -> None:
        if (status, None, None) != (self.status, self.planned_start, self.forecast):
            # A start and a forecast that no longer hold leave the display
            # together with the status.
            self.status, self.planned_start, self.forecast = status, None, None
            self._notify()

    def _notify(self) -> None:
        self._runtime.coordinator.async_update_listeners()
