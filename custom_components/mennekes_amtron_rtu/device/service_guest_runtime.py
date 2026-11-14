"""Guest permission for the current plug-in, using ordinary control and shared limits."""
from __future__ import annotations

import asyncio
import hashlib
import math
import secrets
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .control import AmtronControl
from .enums import GridPhasesConnected, SwitchedPhases
from .errors import DeviceError
from .service_console_runtime import ConsoleModbusService
from .session_mark import SessionMark
from .vehicle import snapshot_vehicle_connected

_STAMP_TOLERANCE_SECONDS = 24 * 3600.0
_STATUS_KEYS = ("error_code", "lock_evse", "charging_release")

@dataclass(frozen=True, slots=True)
class ConnectionSample:
    """A passive snapshot. ``scope`` is a per-connection opaque digest, not a VIN."""

    now: datetime
    available: bool
    present: bool | None
    scope: str | None
    total_kwh: float | None
    current_a: float | None
    current_cap: float
    pending: bool = False
    # False while the error, lock or release value is held from a lost reply.
    status_known: bool = True
    # The wallbox's record of the current plug-in, None until read in full.
    session: SessionMark | None = None


class GuestModbusService(ConsoleModbusService):
    """No guest file, order, energy baseline or separate current controller."""
    _connection_scope: str | None = None
    _connection_unknown_since: float | None = None
    _connection_session: dict[str, Any] | None = None
    # Also fences the vehicle planner after a manual guest stop, until unplug.
    _guest_scope: str | None = None

    def _configure_control(self, control: AmtronControl) -> None:
        control.guest_active = lambda: self._guest_scope is not None
        control.time_limit_reached = self._guest_time_reached

    @property
    def guest_deadline(self) -> datetime | None:
        departure = getattr(self, "departure", None)
        return departure.trip.deadline if departure is not None and departure.ready else None

    def _guest_time_reached(self) -> bool:
        deadline = self.guest_deadline
        return self._guest_scope is not None and deadline is not None and deadline <= datetime.now(UTC)

    @property
    def guest_active(self) -> bool:
        return (self._guest_scope is not None and self.control is not None
                and self.control.pause_requested is not True and not self.control.goal_stop_requested
                and not self._guest_time_reached())

    @property
    def guest_revision(self) -> str:
        departure = getattr(self, "departure", None)
        selection = departure.choice_revision if departure is not None and departure.ready else "loading"
        raw = f"{self.choice_revision}|{selection}|{self._connection_scope}|{self._guest_scope}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def command(self, payload: dict[str, Any], *, _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None) -> dict[str, Any]:
        if payload.get("command") != "guest_start":
            return await super().command(payload, _soc_outcome=_soc_outcome)
        if payload.keys() != {"command", "expected_serial_number", "expected_guest_revision"}:
            raise ValueError("invalid guest command")
        async with self._command_lock:
            if payload["expected_serial_number"] != self.identity.serial_number:
                raise DeviceError("device_identity_changed")
            control = self.control
            if control is None or self._stopping or not self._is_available():
                raise DeviceError("service_unavailable")
            if payload["expected_guest_revision"] != self.guest_revision:
                raise DeviceError("soc_goal_source_changed")
            sample = self._connection_sample()
            if (control.read_only or sample.present is not True or sample.scope is None
                    or not sample.status_known or self.data.fresh("error_code") != 0
                    or self.data.fresh("lock_evse") != 0):
                raise DeviceError("guest_start_not_permitted")
            deadline = self.guest_deadline
            if deadline is not None and deadline <= sample.now:
                raise DeviceError("guest_deadline_expired")
            self._guest_scope = sample.scope
            try:
                await control.async_cancel_timed_current("departure")
                await control.async_set_charging_release(True)
                await control.async_resume_charging(current_cap=self.config.current_cap)
            finally:
                # Only ordinary wallbox intent and existing limits are persisted.
                await self._persist()
            await self.refresh()
            return self._publish()

    async def _guest_tick(self) -> None:
        async with self._command_lock:
            control = self.control
            if (control is None or self._stopping or not self._is_available()
                    or not self._guest_time_reached() or control.pause_requested is True):
                return
            try:
                await control.async_pause_charging()
            finally:
                await self._persist()

    async def _guest_loop(self) -> None:
        while True:
            with suppress(DeviceError, OSError):
                await self._guest_tick()
            await asyncio.sleep(1)

    async def _run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._run_bus(), name="amtron_bus_owner")
            group.create_task(self._guest_loop(), name="amtron_guest_limits")

    async def _run_bus(self) -> None:
        await super()._run()
        raise RuntimeError("Modbus bus owner stopped unexpectedly")

    def _connection_sample(self) -> ConnectionSample:
        control = self.control
        available = self._is_available() and not self._stopping
        present = snapshot_vehicle_connected(
            self.data.fresh, self.identity.layout_version,
        ) if available else None
        # Policy checks run before every write; read the sample, not a snapshot.
        sample = self._energy_sample
        now = datetime.now(UTC)
        total = sample.get("value") if isinstance(sample, dict) else None
        stamp = sample.get("observed_at") if isinstance(sample, dict) else None
        # Judged on the monotonic clock: a wall-clock step must not pause a
        # charge for a meter reading that is not old at all. The stamp itself
        # only has to be a plausible time.
        age = time.monotonic() - self._energy_sample_at if self._energy_sample_at is not None else None
        fresh = (
            available and isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and total >= 0
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and math.isfinite(stamp) and abs(now.timestamp() - stamp) <= _STAMP_TOLERANCE_SECONDS
            and age is not None and 0 <= age <= max(90, self.config.scan_interval * 3)
        )
        scope = session = None
        if control is not None and self._connection_scope is not None:
            selection = "physical-connection"
            scope = hashlib.sha256(f"{self._connection_scope}|{selection}".encode()).hexdigest()
            reading = self._connection_session
            if available and reading is not None and reading.keys() >= {"at", "duration_s", "energy_kwh", "count"}:
                session = SessionMark(
                    reading["at"], reading["duration_s"], reading["energy_kwh"], reading["count"],
                    hashlib.sha256(selection.encode()).hexdigest(),
                )
        current = self.data.fresh("charging_current_ems")
        valid_current = (
            available and isinstance(current, (int, float)) and not isinstance(current, bool)
            and math.isfinite(current) and current >= 0
            and self.data.current_write_generation == self.client.write_state.current_write_generation
        )
        # One lost reply holds these values: no stop can be told from a gap.
        status_known = not any(key in self.data.held for key in _STATUS_KEYS)
        current_value = float(current) if valid_current and isinstance(current, (int, float)) else None
        total_value = float(total) if fresh and isinstance(total, (int, float)) else None
        return ConnectionSample(
            now, available, present, scope, total_value, current_value,
            control.max_charging_current if control is not None else 6,
            control is not None and control.pending_action is not None,
            session=session, status_known=status_known,
        )

    def _soc_observation_scope(self) -> str | None:
        sample = self._connection_sample()
        return sample.scope if sample.available and sample.present is True else None

    def _note_session(self) -> None:
        """Keep the newest wallbox reading of this connection's plug-in."""
        data, reading = self.data, dict(self._connection_session or {})
        duration, energy = data.fresh("session_duration"), data.fresh("session_energy")
        if (
            data.session_read and type(duration) is int and duration >= 0
            and isinstance(energy, float) and math.isfinite(energy) and energy >= 0
        ):
            reading.update(at=datetime.now(UTC), duration_s=duration, energy_kwh=energy)
        count = data.fresh("sessions_total")
        if data.energy_read and type(count) is int and count >= 0:
            reading["count"] = count
        self._connection_session = reading

    async def refresh(self) -> None:
        polled = False
        try:
            await super().refresh()
            polled = True
        finally:
            present = snapshot_vehicle_connected(
                self.data.fresh, self.identity.layout_version,
            ) if self._is_available() else None
            if present is not True:
                self._soc_reading = None
            if present is True:
                self._connection_unknown_since = None
                if self._connection_scope is None:
                    self._connection_scope = secrets.token_hex(32)
            elif present is False:
                self._connection_scope = self._connection_unknown_since = None
            elif self._connection_unknown_since is None:
                self._connection_unknown_since = time.monotonic()
            elif time.monotonic() - self._connection_unknown_since >= 60:
                # Only a lasting gap could hide an unplug; a new car needs
                # fresh permission or the wallbox's proof of the same plug-in.
                self._connection_scope = None
            if self._connection_scope is None:
                self._connection_session = None
            elif polled:
                self._note_session()
            if self._guest_scope is not None and self._connection_sample().scope != self._guest_scope:
                self._guest_scope = None


    def _rate_phases(self) -> int:
        switched = self.data.fresh("switched_phases")
        grid = self.data.fresh("grid_phases_connected")
        detected = self.data.fresh("detected_ev_phases")
        # Detection is the vehicle maximum for the session, not the presently
        # selected relay configuration. Never train single-phase data as three.
        if isinstance(switched, bool):
            return 0
        if switched == SwitchedPhases.SINGLE_PHASE:
            return 1
        if switched != SwitchedPhases.ALL_AVAILABLE or isinstance(grid, bool):
            return 0
        if grid == GridPhasesConnected.L1:
            return 1
        if grid == GridPhasesConnected.L1_L2_L3 and isinstance(detected, (int, float)) and not isinstance(detected, bool) and detected in (1, 2, 3):
            return int(detected)
        return 0
