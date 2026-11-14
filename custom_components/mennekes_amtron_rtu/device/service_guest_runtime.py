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

from .client_errors import AmtronBusError
from .command_events import CommandEvent
from .control import AmtronControl
from .errors import DeviceError
from .guest_permission import GuestPermission
from .power_window import observed_phases
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
    _guest_restore_pending = False
    _guest_ready = False

    def _configure_control(self, control: AmtronControl) -> None:
        self._guest_ready = False
        self._guest_restore_pending = self.client.write_state.guest_permission is not None
        control.guest_active = lambda: self.guest_mode
        control.guest_confirmation_required = lambda: self._guest_restore_pending
        control.time_limit_reached = self._guest_time_reached

    @property
    def guest_mode(self) -> bool:
        return self._guest_scope is not None or self._guest_restore_pending

    async def _initialize_dependents(self) -> None:
        await super()._initialize_dependents()
        permission = self.client.write_state.guest_permission
        if permission is None:
            self._guest_ready = True
            return
        sample = self._connection_sample()
        if sample.present is False:
            self._clear_guest()
        elif (sample.present is True and sample.scope is not None
              and permission.session is not None and sample.session is not None
              and permission.session.continues(sample.session)
              and permission.deadline == self.guest_deadline):
            self._guest_scope = sample.scope
            self._guest_restore_pending = False
        # Missing/mismatched evidence remains fenced until an explicit guest
        # start or confirmed unplug. Normal recovery must not open this gate.
        await self._persist()
        self._guest_ready = True

    def _clear_guest(self) -> None:
        self._guest_scope = None
        self._guest_restore_pending = False
        self.client.write_state.guest_permission = None

    async def _persist(self, *, include_session_history: bool = False) -> None:
        permission = self.client.write_state.guest_permission
        if permission is not None and not self._guest_restore_pending:
            self.client.write_state.guest_permission = GuestPermission(permission.session, self.guest_deadline)
        await super()._persist(include_session_history=include_session_history)

    @property
    def guest_deadline(self) -> datetime | None:
        """Shared time supplied by a service with departure controls."""
        return None

    def _guest_time_reached(self) -> bool:
        deadline = self.guest_deadline
        return self.guest_mode and deadline is not None and deadline <= datetime.now(UTC)

    @property
    def guest_active(self) -> bool:
        return (self.guest_mode and self.control is not None
                and (self.control.pause_requested is not True or self.control.is_paused is not True))

    @property
    def target_time_revision(self) -> str | None:
        """No time editor in a guest-only service."""
        return None

    @property
    def guest_revision(self) -> str:
        raw = f"{self.choice_revision}|{self.target_time_revision}|{self._connection_scope}|{self._guest_scope}"
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
            previous = (self._guest_scope, self._guest_restore_pending, self.client.write_state.guest_permission)
            accepted = False

            def outcome(event: CommandEvent) -> None:
                nonlocal accepted
                if event.action in ("enable_charging", "resume") and event.outcome in ("executed", "uncertain"):
                    accepted = True

            unlisten = control.events.listen(outcome)
            self._guest_scope = sample.scope
            self._guest_restore_pending = False
            self.client.write_state.guest_permission = GuestPermission(sample.session, deadline)
            try:
                # Make the ownership fence durable before any charging write.
                await self._persist()
                await control.async_cancel_timed_current("departure")
                await control.async_start_guest_charging(current_cap=self.config.current_cap)
                accepted = True  # Includes the complete deferred guest start.
            except BaseException:
                # A refused command has no guest grant. An applied/uncertain
                # write or a queued release keeps its guards; rolling them
                # back could leave an already charging guest without limits.
                if not accepted and not control.pending_guest_start:
                    (self._guest_scope, self._guest_restore_pending,
                     self.client.write_state.guest_permission) = previous
                raise
            finally:
                unlisten()
                await self._persist()
            await self.refresh()
            return self._publish()

    async def _guest_tick(self) -> None:
        async with self._command_lock:
            control = self.control
            if (control is None or not self._guest_ready or self._stopping or not self._is_available()
                    or not (self._guest_time_reached() or self._guest_restore_pending)):
                return
            try:
                await control.async_pause_for_energy_goal()
            finally:
                await self._persist()

    async def _guest_loop(self) -> None:
        while True:
            with suppress(DeviceError, AmtronBusError, OSError):
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
            if self.control is not None and present is False:
                self._clear_guest()
            elif self._guest_scope is not None and self._connection_sample().scope != self._guest_scope:
                # A lasting outage may hide a replug. Keep a stop fence rather
                # than silently returning the guest to a vehicle planner.
                self._guest_scope = None
                self._guest_restore_pending = True


    def _rate_phases(self) -> int:
        return observed_phases(self.data.fresh) or 0
