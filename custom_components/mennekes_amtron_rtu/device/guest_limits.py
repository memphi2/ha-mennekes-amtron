"""Guest permission for the current plug-in, using ordinary control and shared limits."""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .client_errors import AmtronBusError
from .command_events import CommandEvent
from .commands import COMMANDS, ENVELOPE
from .connection_sample import connection_sample
from .control import AmtronControl
from .errors import DeviceError, device_error
from .guest_permission import GuestPermission

if TYPE_CHECKING:
    from .service_runtime import ModbusService


class GuestLimits:
    """The guest permission of the one service.

    No guest file, order, energy baseline or separate current controller.
    """

    def __init__(self, service: ModbusService) -> None:
        self._service = service
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
        sample = connection_sample(self._service)
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
        await self._service.save_intent()
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
        raw = f"{self._service.choice_revision}|{self._service.target_time_revision}|{self._service.client.write_state.plug_cycle.scope}|{self.scope}"
        return hashlib.sha256(raw.encode()).hexdigest()

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The guest_start command."""
        if payload.keys() != COMMANDS["guest_start"].fields | ENVELOPE:
            raise ValueError("invalid guest command")
        async with self._service.command_lock:
            if payload["expected_serial_number"] != self._service.identity.serial_number:
                raise DeviceError("device_identity_changed")
            control = self._service.control
            if control is None or self._service.stopping or not self._service.is_available():
                raise DeviceError("service_unavailable")
            if payload["expected_guest_revision"] != self.revision:
                raise DeviceError("soc_goal_source_changed")
            sample = connection_sample(self._service)
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
                await self._service.persist()
                departure_queued = control.pending_owner == "departure"
                await control.async_cancel_departure_current("departure")
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
                await self._service.persist()
            await self._service.refresh()
            return self._service.publish()

    async def tick(self) -> None:
        async with self._service.command_lock:
            control = self._service.control
            if (control is None or not self.ready or self._service.stopping or not self._service.is_available()
                    or not (self.time_reached() or self.restore_pending)):
                return
            try:
                await control.async_pause_for_energy_goal()
            finally:
                await self._service.persist()

    async def loop(self) -> None:
        while True:
            with suppress(DeviceError, AmtronBusError, OSError):
                await self.tick()
            await asyncio.sleep(1)

    def follow(self) -> None:
        """After every poll, failed or not: the permission ends with its connection."""
        sample = connection_sample(self._service)
        if self._service.control is not None and sample.present is False:
            self.clear()
        elif self.scope is not None and sample.scope != self.scope:
            # A lasting outage may hide a replug. Keep a stop fence rather
            # than silently returning the guest to a vehicle planner.
            self.scope = None
            self.restore_pending = True
