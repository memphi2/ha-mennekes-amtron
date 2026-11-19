"""Guest permission for the current plug-in, using ordinary control and shared limits."""
from __future__ import annotations

import asyncio
import hashlib
import math
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .client_errors import AmtronBusError
from .command_events import CommandEvent
from .control import AmtronControl
from .errors import DeviceError, device_error
from .guest_permission import GuestPermission
from .session_mark import SessionMark
from .vehicle import snapshot_vehicle_connected

if TYPE_CHECKING:
    from .service_runtime import ModbusService

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


class GuestLimits:
    """The guest permission of the one service.

    No guest file, order, energy baseline or separate current controller.
    """

    def __init__(self, service: ModbusService) -> None:
        self._service = service
        self.connection_scope: str | None = None
        self.connection_unknown_since: float | None = None
        self.connection_session: dict[str, Any] | None = None
        # Also fences the vehicle planner after a manual guest stop, until unplug.
        self.scope: str | None = None
        self.restore_pending = False
        self.ready = False
    def configure_control(self, control: AmtronControl) -> None:
        self.ready = False
        self.restore_pending = self._service.client.write_state.guest_permission is not None
        control.guest_active = lambda: self.mode
        control.guest_confirmation_required = lambda: self.restore_pending
        control.time_limit_reached = self.time_reached

    @property
    def mode(self) -> bool:
        return self.scope is not None or self.restore_pending

    async def initialize(self) -> None:
        permission = self._service.client.write_state.guest_permission
        if permission is None:
            self.ready = True
            return
        sample = self.connection_sample()
        if sample.present is False:
            self.clear()
        elif (sample.present is True and sample.scope is not None
              and permission.session is not None and sample.session is not None
              and permission.session.continues(sample.session)
              and permission.deadline == self._service.guest_deadline):
            self.scope = sample.scope
            self.restore_pending = False
        # Missing/mismatched evidence remains fenced until an explicit guest
        # start or confirmed unplug. Normal recovery must not open this gate.
        # A disk that refuses the save must not keep the service from starting.
        await self._service._save_intent()
        self.ready = True

    def clear(self) -> None:
        self.scope = None
        self.restore_pending = False
        self._service.client.write_state.guest_permission = None

    def stamp_permission(self) -> None:
        """Before every save: the permission carries the shared target time."""
        permission = self._service.client.write_state.guest_permission
        if permission is not None and not self.restore_pending:
            self._service.client.write_state.guest_permission = GuestPermission(permission.session, self._service.guest_deadline)

    def time_reached(self) -> bool:
        deadline = self._service.guest_deadline
        return self.mode and deadline is not None and deadline <= datetime.now(UTC)

    @property
    def active(self) -> bool:
        return (self.mode and self._service.control is not None
                and (self._service.control.pause_requested is not True or self._service.control.is_paused is not True))

    @property
    def revision(self) -> str:
        raw = f"{self._service.choice_revision}|{self._service.target_time_revision}|{self.connection_scope}|{self.scope}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The guest_start command."""
        if payload.keys() != {"command", "expected_serial_number", "expected_guest_revision"}:
            raise ValueError("invalid guest command")
        async with self._service._command_lock:
            if payload["expected_serial_number"] != self._service.identity.serial_number:
                raise DeviceError("device_identity_changed")
            control = self._service.control
            if control is None or self._service._stopping or not self._service._is_available():
                raise DeviceError("service_unavailable")
            if payload["expected_guest_revision"] != self.revision:
                raise DeviceError("soc_goal_source_changed")
            sample = self.connection_sample()
            if (control.read_only or sample.present is not True or sample.scope is None
                    or not sample.status_known or self._service.data.fresh("error_code") != 0
                    or self._service.data.fresh("lock_evse") != 0):
                raise DeviceError("guest_start_not_permitted")
            deadline = self._service.guest_deadline
            if deadline is not None and deadline <= sample.now:
                raise DeviceError("guest_deadline_expired")
            previous = (self.scope, self.restore_pending, self._service.client.write_state.guest_permission)
            accepted = cancelled = False

            def outcome(event: CommandEvent) -> None:
                nonlocal accepted, cancelled
                if event.action in ("enable_charging", "resume") and event.outcome in ("executed", "uncertain"):
                    accepted = True
                elif event.action == "resume" and event.outcome == "cancelled":
                    cancelled = True

            unlisten = control.events.listen(outcome)
            self.scope = sample.scope
            self.restore_pending = False
            self._service.client.write_state.guest_permission = GuestPermission(sample.session, deadline)
            departure_queued = False
            try:
                # Make the ownership fence durable before any charging write.
                await self._service._persist()
                departure_queued = control.pending_owner == "departure"
                await control.async_cancel_timed_current("departure")
                await control.async_start_guest_charging(current_cap=self._service.config.current_cap)
                if (
                    cancelled and not accepted and not control.pending_guest_start
                    and not control.guest_release_applied
                ):
                    # The car left before the first write: nothing started.
                    raise DeviceError("guest_start_not_permitted")
                accepted = True  # Includes the complete deferred guest start.
            except BaseException as err:
                # A refused command has no guest grant. An applied/uncertain
                # write or a queued release keeps its guards; rolling them
                # back could leave an already charging guest without limits.
                if not accepted and not control.pending_guest_start:
                    (self.scope, self.restore_pending,
                     self._service.client.write_state.guest_permission) = previous
                # A cancelled departure command or an applied write is a
                # change; the console must not report a clean refusal.
                if (accepted or control.pending_guest_start or departure_queued) and isinstance(err, DeviceError):
                    # No caller, the app or HA, may report this as a refusal.
                    raise device_error("guest_start_uncertain") from err
                raise
            finally:
                unlisten()
                await self._service._persist()
            await self._service.refresh()
            return self._service._publish()

    async def tick(self) -> None:
        async with self._service._command_lock:
            control = self._service.control
            if (control is None or not self.ready or self._service._stopping or not self._service._is_available()
                    or not (self.time_reached() or self.restore_pending)):
                return
            try:
                await control.async_pause_for_energy_goal()
            finally:
                await self._service._persist()

    async def loop(self) -> None:
        while True:
            with suppress(DeviceError, AmtronBusError, OSError):
                await self.tick()
            await asyncio.sleep(1)

    def connection_sample(self) -> ConnectionSample:
        control = self._service.control
        available = self._service._is_available() and not self._service._stopping
        present = snapshot_vehicle_connected(
            self._service.data.fresh, self._service.identity.layout_version,
        ) if available else None
        # Policy checks run before every write; read the sample, not a snapshot.
        sample = self._service._energy_sample
        now = datetime.now(UTC)
        total = sample.get("value") if isinstance(sample, dict) else None
        stamp = sample.get("observed_at") if isinstance(sample, dict) else None
        # Judged on the monotonic clock: a wall-clock step must not pause a
        # charge for a meter reading that is not old at all. The stamp itself
        # only has to be a plausible time.
        age = time.monotonic() - self._service._energy_sample_at if self._service._energy_sample_at is not None else None
        fresh = (
            available and isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and total >= 0
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
            and math.isfinite(stamp) and abs(now.timestamp() - stamp) <= _STAMP_TOLERANCE_SECONDS
            and age is not None and 0 <= age <= max(90, self._service.config.scan_interval * 3)
        )
        scope = session = None
        if control is not None and self.connection_scope is not None:
            selection = "physical-connection"
            scope = hashlib.sha256(f"{self.connection_scope}|{selection}".encode()).hexdigest()
            reading = self.connection_session
            if available and reading is not None and reading.keys() >= {"at", "duration_s", "energy_kwh", "count"}:
                session = SessionMark(
                    reading["at"], reading["duration_s"], reading["energy_kwh"], reading["count"],
                    hashlib.sha256(selection.encode()).hexdigest(),
                )
        current = self._service.data.fresh("charging_current_ems")
        valid_current = (
            available and isinstance(current, (int, float)) and not isinstance(current, bool)
            and math.isfinite(current) and current >= 0
            and self._service.data.current_write_generation == self._service.client.write_state.current_write_generation
        )
        # One lost reply holds these values: no stop can be told from a gap.
        status_known = not any(key in self._service.data.held for key in _STATUS_KEYS)
        current_value = float(current) if valid_current and isinstance(current, (int, float)) else None
        total_value = float(total) if fresh and isinstance(total, (int, float)) else None
        return ConnectionSample(
            now, available, present, scope, total_value, current_value,
            control.max_charging_current if control is not None else 6,
            control is not None and control.pending_action is not None,
            session=session, status_known=status_known,
        )

    def soc_observation_scope(self) -> str | None:
        sample = self.connection_sample()
        return sample.scope if sample.available and sample.present is True else None

    def _note_session(self) -> None:
        """Keep the newest wallbox reading of this connection's plug-in."""
        data, reading = self._service.data, dict(self.connection_session or {})
        duration, energy = data.fresh("session_duration"), data.fresh("session_energy")
        if (
            data.session_read and type(duration) is int and duration >= 0
            and isinstance(energy, float) and math.isfinite(energy) and energy >= 0
        ):
            reading.update(at=datetime.now(UTC), duration_s=duration, energy_kwh=energy)
        count = data.fresh("sessions_total")
        if data.energy_read and type(count) is int and count >= 0:
            reading["count"] = count
        self.connection_session = reading

    def after_poll(self, *, polled: bool) -> None:
        """Follow the physical connection after every poll, failed or not."""
        present = snapshot_vehicle_connected(
            self._service.data.fresh, self._service.identity.layout_version,
        ) if self._service._is_available() else None
        if present is not True:
            self._service._soc_reading = None
        if present is True:
            self.connection_unknown_since = None
            if self.connection_scope is None:
                self.connection_scope = secrets.token_hex(32)
        elif present is False:
            self.connection_scope = self.connection_unknown_since = None
        elif self.connection_unknown_since is None:
            self.connection_unknown_since = time.monotonic()
        elif time.monotonic() - self.connection_unknown_since >= 60:
            # Only a lasting gap could hide an unplug; a new car needs
            # fresh permission or the wallbox's proof of the same plug-in.
            self.connection_scope = None
        if self.connection_scope is None:
            self.connection_session = None
        elif polled:
            self._note_session()
        if self._service.control is not None and present is False:
            self.clear()
        elif self.scope is not None and self.connection_sample().scope != self.scope:
            # A lasting outage may hide a replug. Keep a stop fence rather
            # than silently returning the guest to a vehicle planner.
            self.scope = None
            self.restore_pending = True
