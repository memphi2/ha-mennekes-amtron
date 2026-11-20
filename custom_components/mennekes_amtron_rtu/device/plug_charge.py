"""A local, session-scoped EMS release gate; no vehicle authentication protocol."""

from __future__ import annotations

# This controller is the private release-gate component of AmtronControl.
# ruff: noqa: SLF001
import asyncio
import hashlib
import logging
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .client_errors import AmtronBusError, AmtronRateLimitedError
from .data import WallboxData
from .decode import is_hex32
from .errors import DeviceError, DeviceValidationError, validation_error
from .plug_cycle import UNKNOWN_GRACE_SECONDS, PlugChange, PlugCycle
from .registers import (
    AUTHORIZATION_ENABLED,
    CHARGING_RELEASE,
    CP_STATE,
    ERROR_CODE,
    EVSE_STATE,
    LOCK_EVSE,
)
from .vehicle import CONNECTED_EVSE_STATES, vehicle_connected

if TYPE_CHECKING:
    from .client import LockedRegisterReader
    from .control import AmtronControl


from .notice import FailureNotice

_LOGGER = logging.getLogger(__name__)
_POLICY = re.compile(r"(?:[0-9a-f]{64})?")

@dataclass(slots=True)
class PlugChargeState:
    """Only gate configuration and an unapplied manual-release hold are durable.

    The connection itself is the plug cycle's; this is what was granted for it.
    """

    enabled: bool = False
    policy: str = ""
    hold: bool = False
    # A manual release for the current or, without a car, the next plug-in.
    # It opens the gate for any car and ends with that connection.
    manual: bool = False
    authorized: bool = False
    # A manual release read from disk: its connection may have ended while
    # the service was down, so it needs a car at the first answer.
    restored_manual: bool = False
    # The manual release was given to, or met, a connected car. The cycle
    # is not saved, so this says after a restart whose release it was.
    manual_connected: bool = False
    # Renewed with the configuration: a grant asked for under the old one
    # names a session that no longer exists.
    binding: str = field(default_factory=lambda: secrets.token_hex(16))

    @property
    def blocked(self) -> bool:
        return not self.manual and (self.hold or (self.enabled and not self.authorized))

    def session(self, cycle: PlugCycle) -> str | None:
        """The connection under this configuration, which a grant is for; none while the gate is off."""
        if not self.enabled or cycle.id is None:
            return None
        return hashlib.sha256(f"{cycle.id}|{self.binding}".encode()).hexdigest()[:32]

    def invalidate(self, *, ended: bool = True) -> None:
        """No grant is left; ``ended`` when the released connection is over too."""
        if ended:
            self.manual = False
        self.authorized = False
        self.hold |= self.enabled

    def revoke(self) -> None:
        """Withdraw the grant but keep the physical connection.

        Only a confirmed unplug ends a connection. HA may grant the same
        connection again after fresh evidence, without replugging and
        without treating it as a new plug-in.
        """
        if self.authorized:
            _LOGGER.info("Plug & Charge: grant withdrawn; waiting for a fresh grant")
        self.authorized = False
        # An unknown state can hide a different car; a manual release was
        # given for the car that was plugged in.
        self.manual = False
        self.hold |= self.enabled

    def configure(self, enabled: bool, policy: str) -> None:
        if type(enabled) is not bool or not isinstance(policy, str) or _POLICY.fullmatch(policy) is None:
            raise ValueError("invalid Plug & Charge configuration")
        if (self.enabled, self.policy) != (enabled, policy):
            self.enabled, self.policy = enabled, policy
            self.binding = secrets.token_hex(16)
            self.invalidate()

    def follow(self, cycle: PlugCycle, change: PlugChange) -> None:
        """React to what the plug cycle just took in."""
        present = cycle.present
        if present is not None:
            if present is False and self.restored_manual:
                self.manual = False
            self.restored_manual = False
        if not self.enabled or present is False:
            if self.enabled and self.authorized:
                _LOGGER.info("Plug & Charge: grant ended, vehicle unplugged")
            # A release for the next plug-in outlives polls without a car.
            self.invalidate(ended=change.ended)
            return
        if present is None:
            if cycle.id is None:
                self.hold = True
            elif cycle.lapsed and (self.authorized or self.manual):
                _LOGGER.warning(
                    "Plug & Charge: grant withdrawn, vehicle state unknown for over %.0f seconds",
                    UNKNOWN_GRACE_SECONDS,
                )
                self.revoke()
            return
        self.manual_connected = self.manual

    def manual_marks(self, cycle: PlugCycle) -> tuple[bool, bool, bool, str | None]:
        """A release by hand with what is known about it, to take a refused one back."""
        return self.manual, self.manual_connected, self.restored_manual, cycle.id

    def restore_manual(self, marks: tuple[bool, bool, bool, str | None], cycle: PlugCycle) -> None:
        if marks[3] != cycle.id:
            # The connection changed while the command waited: what a poll
            # decided about its release meanwhile stands. Only a release the
            # refused command itself added goes: waiting opened nothing.
            if not marks[0]:
                self.manual = self.manual_connected = self.restored_manual = False
            return
        self.manual, self.manual_connected, self.restored_manual = marks[:3]

    def snapshot(self, cycle: PlugCycle) -> dict[str, Any]:
        session = self.session(cycle)
        since = cycle.since if session is not None else None
        return {
            "enabled": self.enabled, "policy": self.policy, "session": session,
            "authorized": self.authorized, "since": since.isoformat() if since else None,
        }

    def durable(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "policy": self.policy, "hold": self.hold,
                "manual": self.manual,
                # Given for a connected car, not for the next plug-in.
                "manual_connected": self.manual and self.manual_connected}

    @classmethod
    def restore(cls, raw: object) -> PlugChargeState:
        if (
            not isinstance(raw, dict)
            or raw.keys() != {"enabled", "policy", "hold", "manual", "manual_connected"}
            or type(raw["hold"]) is not bool or type(raw["manual"]) is not bool
            or type(raw["manual_connected"]) is not bool or (raw["manual_connected"] and not raw["manual"])
        ):
            raise ValueError("invalid saved Plug & Charge configuration")
        result = cls(hold=raw["hold"])
        result.configure(raw["enabled"], raw["policy"])
        result.manual = raw["manual"] and result.enabled
        result.restored_manual = result.manual_connected = result.manual and raw["manual_connected"]
        return result


class PlugChargeController:
    """Deny independently of manual intent; grant only a still-live session."""

    def __init__(self, control: AmtronControl) -> None:
        self._control = control
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._gate_notice = FailureNotice(_LOGGER, "Plug & Charge")
        self._authorizing: tuple[str, datetime] | None = None

    @property
    def state(self) -> PlugChargeState:
        return self._control.write_state.plug_charge

    @property
    def cycle(self) -> PlugCycle:
        return self._control.write_state.plug_cycle

    def follow(self, change: PlugChange, data: WallboxData | None) -> None:
        """After the plug cycle took a poll, or None for one that failed."""
        if self._stopping:
            return
        self.state.follow(self.cycle, change)
        if self.cycle.present is False:
            self._end_manual_stop()
        # Without a poll there is no bus to close the release on; the first
        # poll that answers does it.
        if self.state.blocked and data is not None and data.get(CHARGING_RELEASE.key) != 0:
            self._schedule_deny()

    def _end_manual_stop(self) -> None:
        """A release switched off by hand ends with its connection.

        The gate already keeps every connection closed until a car is
        recognized. A remembered stop would only refuse every later plug-in
        of the recognized car, unseen behind the closed gate.
        """
        if self.state.enabled and self._control.charging_release_requested is False:
            _LOGGER.info("Plug & Charge: the charging release switched off by hand ended with the connection")
            self._control.charging_release_requested = None

    def _schedule_deny(self) -> None:
        if not self._stopping and not self._control._options.read_only and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._deny(), name="amtron_plug_charge_gate")

    async def _deny(self) -> None:
        try:
            await self.async_sync()
        except (DeviceError, AmtronBusError, OSError) as err:
            self._gate_notice.failed("release gate cannot be enforced", type(err).__name__)
        else:
            self._gate_notice.recovered("release gate enforced again")

    async def async_sync(self) -> None:
        async with self._control.energy_goal_guard:
            await self.async_sync_locked()

    async def async_sync_locked(self) -> None:
        """Enforce the hold while the caller already owns the control lock."""
        # A startup hold forbids starting, but must still close an open gate.
        # Explicit read-only configuration forbids even protective writes.
        if self._stopping or not self.state.blocked or self._control._options.read_only:
            return
        current = await self._control.writes.async_read_switch(CHARGING_RELEASE, stopping=True)
        if current != 0:
            # Gate denial is a stop, never a delayed manual mode transition.
            # Do not overwrite the user's release/pause intent or pending work.
            absent = await self._control.writes.async_vehicle_absent()
            await self._control.writes.async_write(CHARGING_RELEASE, 0, refresh=False)
            self._control.events.record("executed", "disable_charging", source="plug_charge")
            if not absent:
                self._control.write_state.mode_change_at = self._control._monotonic()
        self.state.hold = True

    async def async_configure(self, enabled: bool, policy: str) -> None:
        if enabled:
            self._control._require_write_access()
        async with self._control.energy_goal_guard:
            self.state.configure(enabled, policy)
            self._end_manual_stop()
            await self.async_sync_locked()

    @property
    def authorizing(self) -> bool:
        """Home Assistant's grant is being written."""
        return self._authorizing is not None

    def check_release(self, *, recovery: bool = False) -> None:
        if self._authorizing is not None:
            self._check_request(*self._authorizing)
        elif not self.state.manual and (
            (self.state.enabled and not self.state.authorized) or (recovery and self.state.hold)
        ):
            raise validation_error("plug_charge_blocked")

    def release_written(self) -> None:
        self.state.hold = False

    def set_manual(self, released: bool) -> None:
        """Release the current or next connection by hand, or withdraw that."""
        if released and self.state.enabled and not self.state.manual:
            _LOGGER.info(
                "Plug & Charge: charging released by hand for %s",
                "the next plug-in" if self.cycle.id is None else "this connection",
            )
        self.state.manual = released and self.state.enabled
        self.state.manual_connected = self.state.manual and self.cycle.id is not None
        # Given now, it is not the one read from disk any more.
        self.state.restored_manual = False

    def _check_request(self, session: str, deadline: datetime) -> None:
        if self._stopping or not self.state.enabled:
            raise validation_error("plug_charge_blocked")
        if self.state.session(self.cycle) != session:
            raise validation_error("plug_charge_session_changed")
        if datetime.now(UTC) >= deadline:
            raise validation_error("plug_charge_authorization_expired")
        # A pause holds the current at the pause value, so a grant cannot start
        # a charge. HA ends a manual pause first; a departure plan keeps its own.
        if self._control.charging_release_requested is False:
            raise validation_error("plug_charge_blocked")

    async def _live_presence(self, read: LockedRegisterReader) -> None:
        try:
            await self._check_presence(read)
        except AmtronBusError as err:
            self.state.revoke()
            self._schedule_deny()
            raise validation_error("control_state_unknown") from err

    async def _check_presence(self, read: LockedRegisterReader) -> None:
        evse = await read(EVSE_STATE)
        cp_supported = self._control._identity.layout_version >= CP_STATE.min_layout
        cp = await read(CP_STATE) if cp_supported else None
        present = vehicle_connected(evse, cp, cp_supported=cp_supported)
        if present is not True:
            self.state.follow(self.cycle, self.cycle.observe(present, now=self._control.monotonic()))
            if present is False:
                self._end_manual_stop()
            self._schedule_deny()
            raise validation_error("plug_charge_session_changed")
        if (
            evse not in CONNECTED_EVSE_STATES
            or self._control._identity.layout_version < AUTHORIZATION_ENABLED.min_layout
            or await read(ERROR_CODE) != 0
            or await read(LOCK_EVSE) != 0
            or await read(AUTHORIZATION_ENABLED) != 0
        ):
            raise validation_error("plug_charge_blocked")

    async def async_authorize(self, session: str, deadline: datetime) -> None:
        self._control._require_write_access()
        if not is_hex32(session):
            raise validation_error("plug_charge_session_changed")
        if (
            not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None
            or not 0 < (deadline - datetime.now(UTC)).total_seconds() <= 30
        ):
            raise validation_error("plug_charge_authorization_expired")
        async with self._control.energy_goal_guard:
            self._check_request(session, deadline)
            if self._control.release_stop_requested:
                raise self._control.stop_error()
            await self._live_presence(self._control._client.async_read_register)
            self._check_request(session, deadline)
            current = await self._control.writes.async_read_switch(CHARGING_RELEASE)
            switches = current != 1 and await self._control.writes.async_release_switches_charge()
            if switches:
                try:
                    await self._control.writes.async_check_transition_interval()
                except AmtronRateLimitedError as err:
                    raise validation_error("write_rate_limited", {"seconds": f"{err.retry_after:.0f}"}) from err
            self._check_request(session, deadline)
            self._authorizing = session, deadline
            try:
                # The same write as a release by hand: it counts as a release
                # and an unanswered one restarts the state-change interval.
                # The command reads back afterwards: a poll that fails or
                # outlasts the deadline must not close an acknowledged grant.
                await self._control.writes.async_transition_write(
                    CHARGING_RELEASE, 1, changed=switches, precondition=self._live_presence, refresh=False,
                )
                self._check_request(session, deadline)
                self._control.events.record("executed", "enable_charging", source="plug_charge")
                self.state.authorized = True
            except (DeviceError, AmtronBusError, OSError, asyncio.CancelledError) as err:
                if not isinstance(err, DeviceValidationError) and self.state.session(self.cycle) == session:
                    self.state.revoke()
                self._schedule_deny()
                raise
            finally:
                self._authorizing = None

    async def async_stop(self) -> None:
        self._stopping = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
