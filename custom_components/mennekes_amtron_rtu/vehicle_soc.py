"""Forward optional vehicle percentages without polling or controlling the car."""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from copy import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)

from .device.client_errors import AmtronBusError
from .device.errors import DeviceError
from .device.soc_goal import SocGoal
from .exceptions import service_validation_error

if TYPE_CHECKING:
    from .coordinator import MennekesAmtronCoordinator

_LOGGER = logging.getLogger(__name__)


class SocControl(Protocol):
    """The same source-bound operations in direct and service mode."""

    @property
    def supports_soc_goal(self) -> bool: ...

    @property
    def soc_goal(self) -> SocGoal: ...

    async def async_configure_soc(self, source: str | None) -> None: ...

    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
    ) -> None: ...

    async def async_set_soc_goal(
        self, value: float, *, source: str,
        soc: float | None = None, maximum: float | None = None,
    ) -> None: ...


@runtime_checkable
class TransactionalSocControl(Protocol):
    """Service-only transaction operations, absent from direct controllers."""

    @property
    def soc_goal_revision(self) -> str | None: ...

    @property
    def soc_goal_resolution_pending(self) -> bool: ...

    @property
    def soc_goal_resolution_fenced(self) -> bool: ...

    def abandon_fenced_soc_goal(self) -> None: ...

    async def async_resolve_soc_goal(self) -> str: ...

    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
        expected_soc_revision: str | None = None,
    ) -> None: ...


def vehicle_percentage(state: State | None, *, limit: bool = False) -> float | None:
    """Read reported percentages; unavailable/restored data has no authority."""
    if state is None or state.attributes.get("restored") is True:
        return None
    if state.attributes.get("unit_of_measurement") not in (None, "%"):
        return None
    try:
        value = float(state.state.removesuffix("%").strip())
    except ValueError:
        return None
    if not math.isfinite(value) or not 0 <= value <= 100 or (limit and value == 0):
        return None
    return value


@dataclass(frozen=True, slots=True)
class _Observation:
    soc: float | None
    maximum: float | None


class _Observations:
    """Fold with the real model, retaining at most two outgoing observations."""

    def __init__(self, model: SocGoal, source: str | None, revision: str | None = None) -> None:
        self.model = copy(model)
        self.model.configure_source(source)
        self.revision = revision
        self.minimum: float | None = None
        self.maximum: float | None = None
        self.reached_soc: float | None = None
        self.latest = _Observation(None, None)

    def add(self, observation: _Observation) -> None:
        reached = self.model.stop_requested
        self.model.observe(observation.soc, observation.maximum)
        if not reached and self.model.stop_requested:
            self.reached_soc = observation.soc
        if observation.maximum is not None:
            self.minimum = (
                min(self.minimum, observation.maximum)
                if self.minimum is not None else observation.maximum
            )
            self.maximum = observation.maximum
        self.latest = observation

    def outgoing(self) -> tuple[_Observation, ...]:
        # Preserve a temporary cap reduction without comparing it against an
        # earlier SoC that was never current with that cap. Only the pure model
        # may tell us that an actually observed combination reached the goal.
        current = _Observation(
            self.reached_soc if self.reached_soc is not None else self.latest.soc,
            self.maximum,
        )
        if self.minimum is not None and self.minimum != self.maximum:
            return _Observation(None, self.minimum), current
        return (current,)

    def combine(self, other: _Observations) -> _Observations:
        for observation in other.outgoing():
            self.add(observation)
        self.latest = other.latest
        return self


class VehicleSocObserver:
    """One bounded event worker; missing inputs leave charging unchanged."""

    def __init__(
        self, hass: HomeAssistant, control: SocControl,
        coordinator: MennekesAmtronCoordinator,
        soc_entity: str | None, limit_entity: str | None = None,
    ) -> None:
        self._hass = hass
        self._control = control
        self._coordinator = coordinator
        self._soc_entity = soc_entity
        self._limit_entity = limit_entity
        self.source = f"{soc_entity}|{limit_entity or ''}" if soc_entity else None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._pending: _Observations | None = None
        self._inflight: _Observations | None = None
        self._previous_target_model: SocGoal | None = None
        self._previous_target_revision: str | None = None
        self._target_model: SocGoal | None = None
        self._target_pending: _Observations | None = None
        self._last_sent: _Observation | None = None
        self._task: asyncio.Task[None] | None = None
        self._command_lock = asyncio.Lock()
        self._configured = False
        self._stopping = False
        self._failure_logged = False
        self._was_available = coordinator.last_update_success

    @property
    def latest_soc(self) -> float | None:
        return vehicle_percentage(
            self._hass.states.get(self._soc_entity) if self._soc_entity else None,
        )

    @property
    def maximum(self) -> float | None:
        return vehicle_percentage(
            self._hass.states.get(self._limit_entity) if self._limit_entity else None,
            limit=True,
        )

    async def async_start(self) -> None:
        """Bind the source before accepting observations; fail setup if binding fails."""
        self._unsubscribers.append(
            self._coordinator.async_add_listener(self._coordinator_updated),
        )
        if self._soc_entity:
            entities = [self._soc_entity]
            if self._limit_entity:
                entities.append(self._limit_entity)
            observed_values = _Observation(self.latest_soc, self.maximum)

            @callback
            def observed(event: Event[EventStateChangedData] | Event[EventStateReportedData]) -> None:
                nonlocal observed_values
                # Use the event's value, not a newer global state written by
                # another listener before this event reaches our callback.
                state = event.data["new_state"]
                observed_values = (
                    _Observation(vehicle_percentage(state), observed_values.maximum)
                    if event.data["entity_id"] == self._soc_entity else
                    _Observation(observed_values.soc, vehicle_percentage(state, limit=True))
                )
                self._queue_observation(observed_values)

            self._unsubscribers.extend((
                async_track_state_change_event(self._hass, entities, observed),
                async_track_state_report_event(self._hass, entities, observed),
            ))
        try:
            if self._control.supports_soc_goal:
                async with self._command_lock:
                    await self._control.async_configure_soc(self.source)
                    self._configured = True
            self._queue_current()
            if self._task is not None:
                await asyncio.shield(self._task)
        except BaseException:
            await self.async_stop()
            raise

    @callback
    def _coordinator_updated(self) -> None:
        available = self._coordinator.last_update_success
        recovered = available and not self._was_available
        self._was_available = available
        if recovered or not self._configured:
            self._last_sent = None
            self._queue_current()
        else:
            self._ensure_task()

    @callback
    def _queue_current(self) -> None:
        self._queue_observation(_Observation(self.latest_soc, self.maximum))

    @callback
    def _queue_observation(self, observation: _Observation) -> None:
        if self._stopping:
            return
        if self._target_model is not None:
            if self._target_pending is None:
                self._target_pending = _Observations(self._target_model, self.source)
            self._target_pending.add(observation)
        if self._pending is None and observation == self._last_sent:
            return
        if self._pending is None:
            model = (
                self._previous_target_model or
                (self._inflight.model if self._inflight is not None else self._control.soc_goal)
            )
            revision = (
                self._previous_target_revision if self._previous_target_model is not None else
                self._inflight.revision if self._inflight is not None else
                getattr(self._control, "soc_goal_revision", None)
            )
            self._pending = _Observations(model, self.source, revision)
        self._pending.add(observation)
        self._ensure_task()

    @callback
    def _ensure_task(self) -> None:
        if (
            not self._stopping and (self._pending is not None or self._resolution_pending()) and self._task is None
            and self._control.supports_soc_goal
        ):
            self._task = self._hass.async_create_task(
                self._run(), "amtron_vehicle_soc", eager_start=False,
            )

    async def _run(self) -> None:
        try:
            while (self._pending is not None or self._resolution_pending()) and not self._stopping:
                async with self._command_lock:
                    if self._resolution_pending():
                        transaction = self._transaction_control()
                        if transaction is None:
                            return
                        try:
                            outcome = await transaction.async_resolve_soc_goal()
                        except (HomeAssistantError, DeviceError, AmtronBusError, OSError):
                            return
                        if outcome == "unknown":
                            return
                        if outcome == "applied":
                            self._pending = self._target_pending
                            if self._pending is not None:
                                self._pending.revision = getattr(self._control, "soc_goal_revision", None)
                        elif outcome == "superseded":
                            self._pending = None
                            self._last_sent = None
                        self._clear_target_models()
                        if outcome == "superseded":
                            self._queue_current()
                    observations = self._pending
                    self._pending = None
                    if observations is None:
                        continue
                    revision = getattr(self._control, "soc_goal_revision", None)
                    if observations.revision is not None and revision != observations.revision:
                        self._last_sent = None
                        self._queue_current()
                        continue
                    self._inflight = observations
                    try:
                        if not self._configured:
                            await self._control.async_configure_soc(self.source)
                            self._configured = True
                        if self.source is not None:
                            for observation in observations.outgoing():
                                transaction = self._transaction_control()
                                if observations.revision is not None and transaction is not None:
                                    await transaction.async_observe_soc(
                                        observation.soc, observation.maximum, source=self.source,
                                        expected_soc_revision=observations.revision,
                                    )
                                else:
                                    await self._control.async_observe_soc(
                                        observation.soc, observation.maximum, source=self.source,
                                    )
                    except (HomeAssistantError, DeviceError, AmtronBusError, OSError) as err:
                        self._pending = (
                            observations.combine(self._pending)
                            if self._pending is not None else observations
                        )
                        if not self._failure_logged:
                            _LOGGER.warning("Cannot update vehicle charge target (%s)", type(err).__name__)
                            self._failure_logged = True
                        return
                    self._last_sent = observations.latest
                    self._failure_logged = False
        finally:
            self._inflight = None
            self._task = None

    async def async_set_target(self, value: float) -> None:
        """Use the current sources atomically when the user chooses a target."""
        if self._stopping or self.source is None:
            raise service_validation_error("soc_goal_unavailable")
        if self._task is not None:
            await asyncio.shield(self._task)
        async with self._command_lock:
            if self._resolution_pending():
                transaction = self._transaction_control()
                if transaction is None or not transaction.soc_goal_resolution_fenced:
                    raise service_validation_error("soc_goal_unavailable")
                # The service has fenced the earlier request. An explicit new
                # selection supersedes its unknown outcome and both folds.
                transaction.abandon_fenced_soc_goal()
                self._pending = None
                self._last_sent = None
                self._clear_target_models()
            if (
                value > 0 and self._limit_entity is not None and self.maximum is None
                and self._control.soc_goal.maximum_percent is None
            ):
                raise service_validation_error("soc_goal_limit_unavailable")
            previous = self._pending
            self._pending = None
            soc, maximum = self.latest_soc, self.maximum
            self._previous_target_model = copy(self._control.soc_goal)
            self._previous_target_revision = getattr(self._control, "soc_goal_revision", None)
            original = self._previous_target_model.snapshot()
            candidate_model = copy(self._previous_target_model)
            candidate: SocGoal | None
            try:
                candidate_model.set_target(value, soc, maximum)
            except ValueError:
                # The control remains responsible for validating the request.
                candidate = None
            else:
                candidate = candidate_model
            self._target_model = candidate
            self._target_pending = None
            try:
                await self._control.async_set_soc_goal(
                    value, source=self.source,
                    soc=soc, maximum=maximum,
                )
            except BaseException:
                if self._resolution_pending():
                    if previous is not None:
                        self._pending = previous.combine(self._pending) if self._pending is not None else previous
                elif (
                    candidate is not None and candidate.snapshot() != original
                    and self._control.soc_goal.snapshot() == candidate.snapshot()
                ):
                    # A cancelled or uncertain write can still publish its
                    # candidate; preserve evidence against that live target.
                    self._pending = self._target_pending
                elif previous is not None:
                    self._pending = (
                        previous.combine(self._pending) if self._pending is not None else previous
                    )
                raise
            else:
                # Events received while the command was in flight were also
                # folded against the requested target. A remote snapshot can
                # still contain the old goal until the response arrives.
                if candidate is not None:
                    self._pending = self._target_pending
                    if self._pending is not None:
                        self._pending.revision = getattr(self._control, "soc_goal_revision", None)
            finally:
                if not self._resolution_pending():
                    self._clear_target_models()
                self._ensure_task()

    def _resolution_pending(self) -> bool:
        transaction = self._transaction_control()
        return transaction is not None and transaction.soc_goal_resolution_pending

    def _transaction_control(self) -> TransactionalSocControl | None:
        if isinstance(self._control, TransactionalSocControl):
            return self._control
        return None

    def _clear_target_models(self) -> None:
        self._previous_target_model = None
        self._previous_target_revision = None
        self._target_model = None
        self._target_pending = None

    async def async_stop(self) -> None:
        """Detach inputs and finish cancellation before this owner is unloaded."""
        self._stopping = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._pending = None
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
