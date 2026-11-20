"""Own the shared target time and optional vehicle plan inside the service."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .command_events import attributed
from .control import AmtronControl
from .departure_plan import (
    DEPARTURE_OWNER,
    DepartureController,
    DepartureInputs,
    DepartureSample,
    instant,
)
from .departure_store import DepartureStore
from .errors import DeviceError
from .notice import FailureNotice
from .power_window import observed_phases

if TYPE_CHECKING:
    from .service_runtime import ModbusService

_LOGGER = logging.getLogger(__name__)
_TICK_SECONDS = 5.0
DEPARTURE_COMMANDS = {
    "departure_choice": frozenset({"command", "deadline", "revision", "expected_serial_number"}),
    "departure_trip": frozenset({"command", "deadline", "source", "withdraw", "expected_serial_number", "expected_selection"}),
    "departure_inputs": frozenset({"command", "value", "expected_serial_number"}),
}


class DeparturePlanner:
    """Home Assistant hands its departure over; the service plans and stops it.

    The plan keeps running while Home Assistant is away, and across a restart
    when the wallbox proves the same plug-in.
    """

    def __init__(self, service: ModbusService) -> None:
        self._service = service
        self.controller: DepartureController | None = None
        # One for the service's life: a reconnect is no new failure.
        self._tick_notice = FailureNotice(_LOGGER, "Departure plan")

    @property
    def deadline(self) -> datetime | None:
        """The shared target time, which also limits a guest."""
        return self.controller.trip.deadline if self.controller is not None and self.controller.ready else None

    @property
    def revision(self) -> str | None:
        return self.controller.choice_revision if self.controller is not None and self.controller.ready else None

    def configure_control(self, control: AmtronControl) -> None:
        identity = self._service.identity.serial_number or f"{self._service.config.connection.port}:{self._service.config.connection.device_id}"
        self.controller = DepartureController(
            DepartureStore(self._service.state_path.with_name("departure-plan.json"), identity),
            self._departure_sample, self._apply_departure, self._cancel_departure,
            self._end_departure_pause,
        )

    async def initialize(self) -> None:
        if self.controller is None:
            raise RuntimeError("departure plan was not configured")
        await self.controller.initialize()

    def _departure_sample(self) -> DepartureSample:
        connection = self._service.guest.connection_sample()
        control = self._service.control
        energy = self._service._energy_sample
        stamp = energy.get("observed_at") if energy is not None and connection.total_kwh is not None else None
        goal = control.soc_goal if control is not None else None
        return DepartureSample(
            now=connection.now, available=connection.available, present=connection.present,
            scope=connection.scope, session=connection.session,
            total_kwh=connection.total_kwh, meter_at=float(stamp) if stamp is not None else None,
            applied_a=connection.current_a, max_current_a=connection.current_cap,
            paused=control.is_paused if control is not None else None,
            pause_requested=control.pause_requested if control is not None else None,
            pending=connection.pending,
            read_only=control.read_only if control is not None else self._service.config.read_only,
            vehicle=goal.source if goal is not None else None,
            target_percent=goal.target_percent if goal is not None else 0.0,
            goal_status=goal.status if goal is not None else "disabled",
            target_is_limit=goal is not None and goal.target_percent == goal.maximum_percent,
            energy_reached=control is not None and control.energy_goal.status == "reached",
            goal_stop=control is not None and control.energy_goal_stop_requested,
            guest_mode=self._service.guest.mode,
            phases=observed_phases(self._service.data.fresh) or None,
            pending_foreign=(
                control is not None and control.pending_action is not None
                and control.pending_owner != DEPARTURE_OWNER
            ),
        )

    async def _apply_departure(self, value: float | None, deadline: datetime) -> None:
        await self._departure_write(
            lambda control: control.async_set_timed_current(value, deadline=deadline, owner=DEPARTURE_OWNER),
        )

    @attributed("departure_plan")
    async def _end_departure_pause(self) -> None:
        cap = self._service.config.current_cap
        await self._departure_write(lambda control: control.async_resume_charging(current_cap=cap))

    async def _departure_write(self, write: Callable[[AmtronControl], Any]) -> None:
        async with self._service._command_lock:
            control = self._service.control
            if control is None or self._service._stopping or not self._service._is_available():
                raise DeviceError("service_unavailable")
            # The plan decided before awaiting this lock. A guest may have
            # taken over meanwhile, including a deliberately paused guest.
            # Check ownership here for every plan write, including resumes
            # that end a pause the plan originally set.
            if self._service.guest.mode:
                raise DeviceError("soc_goal_source_changed")
            try:
                await write(control)
            finally:
                await self._service._persist()

    async def _cancel_departure(self) -> None:
        # Queue bookkeeping only; it needs no bus and works in read-only mode.
        async with self._service._command_lock:
            if self._service.control is not None:
                await self._service.control.async_cancel_timed_current(DEPARTURE_OWNER)

    async def loop(self) -> None:
        while True:
            departure = self.controller
            if departure is None:
                await asyncio.sleep(_TICK_SECONDS)
                continue
            departure.wake.clear()
            try:
                await departure.tick()
                self._tick_notice.recovered("tick works again")
            except Exception as err:  # noqa: BLE001
                # The bus owner shares this task group; an unforeseen failure
                # must not stop the service. The plan writes nothing meanwhile.
                self._tick_notice.failed(
                    "tick failed", type(err).__name__, level=logging.ERROR, exc_info=True,
                )
                departure.status = "error"
            with suppress(TimeoutError):
                await asyncio.wait_for(departure.wake.wait(), _TICK_SECONDS)

    async def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One of DEPARTURE_COMMANDS."""
        name = payload["command"]
        if payload.keys() - DEPARTURE_COMMANDS[name]:
            raise ValueError("unknown command field")
        if "expected_serial_number" not in payload:
            raise ValueError("expected_serial_number is required")
        departure = self.controller
        if departure is None or self._service._stopping:
            raise DeviceError("service_unavailable")
        if payload["expected_serial_number"] != self._service.identity.serial_number:
            raise DeviceError("device_identity_changed")
        # Plan data only: no bus access, so it is accepted while the wallbox
        # is unreachable, like the charge level Home Assistant forwards.
        if name == "departure_choice":
            revision = payload.get("revision")
            deadline = payload.get("deadline")
            if not isinstance(revision, str) or (deadline is not None and not isinstance(deadline, str)):
                raise ValueError("invalid departure choice")
            if self._service.control is None:
                raise DeviceError("service_unavailable")
            await departure.set_trip(
                None if deadline is None else instant(deadline), None,
                expected_revision=revision, expected_vehicle=self._service.control.soc_goal.source,
                editor="app",
            )
        elif name == "departure_trip":
            deadline, source = payload.get("deadline"), payload.get("source")
            withdraw = payload.get("withdraw", False)
            if (
                (deadline is not None and not isinstance(deadline, str))
                or (source is not None and not isinstance(source, str))
                or type(withdraw) is not bool
            ):
                raise ValueError("invalid departure trip")
            await departure.set_trip(
                None if deadline is None else instant(deadline), source, withdraw=withdraw, expected_selection=payload.get("expected_selection"), vehicle_handover=True,
            )
        else:
            await departure.set_inputs(DepartureInputs.restore(payload.get("value")))
        if name == "departure_choice":
            await self._service._persist()
        if self._service._command_lock.locked():
            # Another command still runs: a patient reader must not get the
            # state from before this plan change.
            self._service._published = None
            return self._service.snapshot()
        return self._service._publish()

    async def shutdown(self) -> None:
        if self.controller is not None:
            await self.controller.shutdown()
