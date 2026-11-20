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
from .device.departure_plan import DepartureForecast, DepartureInputs
from .device.departure_step import (
    minimum_level,
)
from .device.enums import EvseState
from .device.power_window import car_phases, observed_phases
from .device.registers import EVSE_STATE
from .device.timed_goal import TimedGoal, matches_departure_source
from .exceptions import home_assistant_error, service_validation_error
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
from .vehicle_trips import VehicleTripMemory

if TYPE_CHECKING:
    from .entry_types import MennekesAmtronConfigEntry

from .device.notice import FailureNotice

_LOGGER = logging.getLogger(__name__)


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
    """Keep the service's plan for the selected car supplied.

    The service holds the one target time. This takes it from a calendar,
    the car or an entity, hands over the car's data, and shows the plan.
    """

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._options = vehicle_options(entry.options)
        self._runtime = entry.runtime_data
        # The trip this car had when another one was selected is still to be given back.
        self._resume = self._options.get(CONF_VEHICLE_PROFILE_ID) is not None
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
        # The selected car's phases from its last charge, if it had one.
        self._known_phases: int | None = None

    @property
    def goal(self) -> TimedGoal:
        """The service's trip; an empty one while it is not known."""
        remote = self._runtime.control.departure
        return remote.trip if remote is not None else TimedGoal()

    async def async_start(self) -> None:
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
        expected: dict[str, Any] | None = None,
    ) -> None:
        if self._stopping:
            return
        if self._runtime.control.guest_active():
            await self._runtime.control.async_set_target_time(value)
            return
        await self._set_deadline(value, source=source, expected=expected)

    def _selection(self) -> dict[str, Any] | None:
        """What the service's trip is now, to refuse an edit made on an older one."""
        remote = self._runtime.control.departure
        return remote.selection() if remote is not None else None

    async def _set_deadline(
        self, value: datetime | None, *, source: str | None,
        expected: dict[str, Any] | None = None,
    ) -> None:
        if value is not None and (value.utcoffset() is None or value <= dt_util.utcnow()):
            raise service_validation_error("target_time_not_future")
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
        # The time was checked above; only a malformed source, which no
        # user input can produce, still fails here.
        if not TimedGoal.restore(original.snapshot()).set(value, source):
            return
        await self._runtime.control.async_set_departure_trip(value, source, expected_selection=expected)
        # A new trip plans its own start; the old one no longer applies.
        self.planned_start = None
        self._notify()
        self.request_evaluate()

    async def async_use_next_calendar_event(self) -> None:
        """Select a single matching future event; later events never roll in."""
        entity_id = self._options.get(CONF_TIMED_CALENDAR_ENTITY)
        title = self._options.get(CONF_TIMED_CALENDAR_TITLE)
        if not isinstance(entity_id, str) or not isinstance(title, str) or not title:
            raise service_validation_error("calendar_not_configured")
        now = dt_util.utcnow()
        end = now + timedelta(days=14)
        original = self._selection()
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
        original = self._selection()
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
        car = car_phases(data.fresh)
        if car is not None and data.fresh(EVSE_STATE.key) == EvseState.CHARGING:
            # Only a drawing car proves the reading is its own.
            self._remember_phases(car)
        # Detection is zero while the car draws no current, i.e. all the time
        # before a late start. The car's last detected phases then decide, and
        # a car never seen charging is assumed three-phase. A one-phase car
        # corrects that on its first charge, from the reserve the start keeps.
        return observed_phases(data.fresh) or self._known_phases or 3

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
        control = self._runtime.control
        if control.departure is not None and not control.guest_active():
            await self._resume_trip()
            await self._follow_source()
        await self._follow_service()

    async def _resume_trip(self) -> None:
        """Give this car back the trip it had before another car was selected."""
        if not self._resume:
            return
        profile = self._options[CONF_VEHICLE_PROFILE_ID]
        trips = VehicleTripMemory(
            self._hass, self._entry.entry_id, self._runtime.identity.serial_number or "",
        )
        trip = await trips.async_peek(profile)
        if trip is not None and self.goal.deadline is None:
            await self._runtime.control.async_set_departure_trip(trip.deadline, trip.source)
        # Handed over, or no longer wanted: the service has the trip now.
        await trips.async_swap(None, None, profile)
        self._resume = False

    async def _follow_source(self) -> None:
        """Take a new time from the entity that names the next departure."""
        source_entity = self._options.get(CONF_TIMED_DEPARTURE_ENTITY)
        if not source_entity:
            return
        value = departure_time(self._hass.states.get(source_entity))
        if value is None or value <= dt_util.utcnow():
            return
        if not matches_departure_source(self.goal.source, source_entity, value):
            await self._set_deadline(value, source=f"entity:{source_entity}:{value.isoformat()}")

    async def _follow_service(self) -> None:
        """Hand the car's data to the service, which plans; show its state."""
        control = self._runtime.control
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
