"""Plan Home Assistant's departure inside the service, beside its orders."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from typing import Any

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
from .operation import summarize
from .service_forecast_runtime import ForecastModbusService

_LOGGER = logging.getLogger(__name__)
_TICK_SECONDS = 5.0
_FIELDS = {
    "departure_trip": frozenset({"command", "deadline", "source", "withdraw", "expected_serial_number"}),
    "departure_inputs": frozenset({"command", "value", "expected_serial_number"}),
}


class DepartureModbusService(ForecastModbusService):
    """Home Assistant hands its departure over; the service plans and stops it.

    The plan keeps running while Home Assistant is away, and across a restart
    when the wallbox proves the same plug-in.
    """

    departure: DepartureController | None = None

    def _configure_control(self, control: AmtronControl) -> None:
        super()._configure_control(control)
        identity = self.identity.serial_number or f"{self.config.connection.port}:{self.config.connection.device_id}"
        self.departure = DepartureController(
            DepartureStore(self.state_path.with_name("departure-plan.json"), identity),
            self._departure_sample, self._apply_departure, self._cancel_departure,
            self._end_departure_pause,
        )

    async def _initialize(self) -> None:
        await super()._initialize()
        if self.departure is None:
            raise RuntimeError("departure plan was not configured")
        await self.departure.initialize()

    def _departure_sample(self) -> DepartureSample:
        job = self._job_inputs()
        control = self.control
        energy = self._energy_sample
        stamp = energy.get("observed_at") if energy is not None and job.total_kwh is not None else None
        goal = control.soc_goal if control is not None else None
        return DepartureSample(
            now=job.now, available=job.available, present=job.present,
            scope=job.scope, session=job.session,
            total_kwh=job.total_kwh, meter_at=float(stamp) if stamp is not None else None,
            applied_a=job.current_a, max_current_a=job.current_cap,
            paused=control.is_paused if control is not None else None,
            pause_requested=control.pause_requested if control is not None else None,
            pending=job.pending,
            read_only=control.read_only if control is not None else self.config.read_only,
            vehicle=goal.source if goal is not None else None,
            target_percent=goal.target_percent if goal is not None else 0.0,
            goal_status=goal.status if goal is not None else "disabled",
            target_is_limit=goal is not None and goal.target_percent == goal.maximum_percent,
            energy_reached=control is not None and control.energy_goal.status == "reached",
            goal_stop=control is not None and control.energy_goal_stop_requested,
            order_active=self.jobs is not None and self.jobs.holding,
            phases=self._rate_phases() or None,
            pending_foreign=(
                control is not None and control.pending_action is not None
                and control.pending_owner != DEPARTURE_OWNER
            ),
        )

    async def _apply_departure(self, value: float | None, deadline: datetime) -> None:
        await self._departure_write(
            lambda control: control.async_set_timed_current(value, deadline=deadline, owner=DEPARTURE_OWNER),
        )

    async def _end_departure_pause(self) -> None:
        cap = self.config.current_cap
        await self._departure_write(lambda control: control.async_resume_charging(current_cap=cap))

    async def _departure_write(self, write: Callable[[AmtronControl], Any]) -> None:
        async with self._command_lock:
            control = self.control
            if control is None or self._stopping or not self._is_available():
                raise DeviceError("service_unavailable")
            try:
                await write(control)
            finally:
                await self._persist()

    async def _cancel_departure(self) -> None:
        # Queue bookkeeping only; it needs no bus and works in read-only mode.
        async with self._command_lock:
            if self.control is not None:
                await self.control.async_cancel_timed_current(DEPARTURE_OWNER)

    async def refresh(self) -> None:
        try:
            await super().refresh()
        finally:
            if self.departure is not None:
                self.departure.observe()

    async def _jobs_loop(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(super()._jobs_loop(), name="amtron_charge_jobs")
            group.create_task(self._departure_loop(), name="amtron_departure")

    async def _departure_loop(self) -> None:
        while True:
            departure = self.departure
            if departure is None:
                await asyncio.sleep(_TICK_SECONDS)
                continue
            departure.wake.clear()
            try:
                await departure.tick()
            except Exception:
                # The bus owner shares this task group; an unforeseen failure
                # must not stop the service. The plan writes nothing meanwhile.
                _LOGGER.exception("Departure plan tick failed")
                departure.status = "error"
            with suppress(TimeoutError):
                await asyncio.wait_for(departure.wake.wait(), _TICK_SECONDS)

    async def command(
        self, payload: dict[str, Any], *,
        _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None,
    ) -> dict[str, Any]:
        name = payload.get("command")
        if name not in _FIELDS:
            return await super().command(payload, _soc_outcome=_soc_outcome)
        if payload.keys() - _FIELDS[name]:
            raise ValueError("unknown command field")
        if "expected_serial_number" not in payload:
            raise ValueError("expected_serial_number is required")
        departure = self.departure
        if departure is None or self._stopping:
            raise DeviceError("service_unavailable")
        if payload["expected_serial_number"] != self.identity.serial_number:
            raise DeviceError("device_identity_changed")
        # Plan data only: no bus access, so it is accepted while the wallbox
        # is unreachable, like the charge level Home Assistant forwards.
        if name == "departure_trip":
            deadline, source = payload.get("deadline"), payload.get("source")
            withdraw = payload.get("withdraw", False)
            if (
                (deadline is not None and not isinstance(deadline, str))
                or (source is not None and not isinstance(source, str))
                or type(withdraw) is not bool
            ):
                raise ValueError("invalid departure trip")
            await departure.set_trip(
                None if deadline is None else instant(deadline), source, withdraw=withdraw,
            )
        else:
            await departure.set_inputs(DepartureInputs.restore(payload.get("value")))
        if self._command_lock.locked():
            # Another command still runs: a patient reader must not get the
            # state from before this plan change.
            self._published = None
            return self.snapshot()
        return self._publish()

    def snapshot(self) -> dict[str, Any]:
        result = super().snapshot()
        departure = self.departure
        # None while the plan is loading; Home Assistant then waits instead
        # of planning on its own.
        result["control"] = {
            **result["control"],
            "supports_departure_plan": True,
            "departure": departure.snapshot() if departure is not None and departure.ready else None,
        }
        jobs = self.jobs
        # The charging job as its own page shows it; None while it loads.
        result["control"]["orders"] = jobs.snapshot() if jobs is not None and jobs.ready else None
        result["control"]["operation"] = self._operation(result)
        return result

    def _operation(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Who controls the charge, why it waits and what comes next."""
        control = self.control
        orders = snapshot["control"]["orders"]
        sample = self._job_inputs()
        return summarize(
            available=snapshot["available"] is True, present=sample.present,
            control=snapshot["control"], departure=snapshot["control"]["departure"],
            job=orders["job"] if orders is not None else None,
            # Only a charge target's stop is a reached target; an order names its own.
            goal_stop=control is not None and control.goal_stop_requested,
        )

    async def stop(self) -> None:
        try:
            if self.departure is not None:
                await self.departure.shutdown()
        finally:
            await super().stop()
