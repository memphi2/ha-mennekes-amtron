"""A local, session-scoped EMS release gate; no vehicle authentication protocol."""

from __future__ import annotations

# This controller is the private release-gate component of AmtronControl.
# ruff: noqa: SLF001
import asyncio
import logging
import re
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .client_errors import AmtronBusError, AmtronRateLimitedError
from .data import WallboxData
from .errors import DeviceError, DeviceValidationError, validation_error
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

_LOGGER = logging.getLogger(__name__)
_POLICY = re.compile(r"(?:[0-9a-f]{64})?")
_SESSION = re.compile(r"[0-9a-f]{32}")


@dataclass(slots=True)
class PlugChargeState:
    """Only gate configuration and an unapplied manual-release hold are durable."""

    enabled: bool = False
    policy: str = ""
    hold: bool = False
    session: str | None = None
    authorized: bool = False
    since: datetime | None = None
    last_absent_at: datetime | None = None

    @property
    def blocked(self) -> bool:
        return self.hold or (self.enabled and not self.authorized)

    def invalidate(self) -> None:
        self.session = None
        self.since = None
        self.last_absent_at = None
        self.authorized = False
        self.hold |= self.enabled

    def configure(self, enabled: bool, policy: str) -> None:
        if type(enabled) is not bool or not isinstance(policy, str) or _POLICY.fullmatch(policy) is None:
            raise ValueError("invalid Plug & Charge configuration")
        if (self.enabled, self.policy) != (enabled, policy):
            self.enabled, self.policy = enabled, policy
            self.invalidate()

    def observe(self, present: bool | None, *, observed_at: datetime | None = None) -> None:
        if not self.enabled or present is not True:
            self.invalidate()
            if self.enabled and present is False:
                self.last_absent_at = observed_at or datetime.now(UTC)
        elif self.session is None:
            self.session = secrets.token_hex(16)
            self.since = self.last_absent_at or datetime.now(UTC)

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "policy": self.policy, "session": self.session, "authorized": self.authorized, "since": self.since.isoformat() if self.since else None}

    def durable(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "policy": self.policy, "hold": self.hold}

    @classmethod
    def restore(cls, raw: object) -> PlugChargeState:
        if not isinstance(raw, dict) or raw.keys() != {"enabled", "policy", "hold"} or type(raw["hold"]) is not bool:
            raise ValueError("invalid saved Plug & Charge configuration")
        result = cls(hold=raw["hold"])
        result.configure(raw["enabled"], raw["policy"])
        return result


class PlugChargeController:
    """Deny independently of manual intent; grant only a still-live session."""

    def __init__(self, control: AmtronControl) -> None:
        self._control = control
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._failed = False
        self._authorizing: tuple[str, datetime] | None = None

    @property
    def state(self) -> PlugChargeState:
        return self._control.write_state.plug_charge

    def observe(self, data: WallboxData | None, *, observed_at: datetime | None = None) -> None:
        if self._stopping:
            return
        present = None if data is None else vehicle_connected(
            data.get(EVSE_STATE.key), data.get(CP_STATE.key),
            cp_supported=self._control._identity.layout_version >= CP_STATE.min_layout,
        )
        self.state.observe(present, observed_at=observed_at)
        if self.state.blocked and (data is None or data.get(CHARGING_RELEASE.key) != 0):
            self._schedule_deny()

    def _schedule_deny(self) -> None:
        if not self._stopping and not self._control.read_only and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._deny(), name="amtron_plug_charge_gate")

    async def _deny(self) -> None:
        try:
            await self.async_sync()
        except (DeviceError, AmtronBusError, OSError) as err:
            if not self._failed:
                _LOGGER.warning("Cannot enforce Plug & Charge release gate (%s)", type(err).__name__)
                self._failed = True
        else:
            self._failed = False

    async def async_sync(self) -> None:
        async with self._control.energy_goal_guard:
            await self.async_sync_locked()

    async def async_sync_locked(self) -> None:
        """Enforce the hold while the caller already owns the control lock."""
        if self._stopping or not self.state.blocked or self._control.read_only:
            return
        current = await self._control._async_read_switch(CHARGING_RELEASE, stopping=True)
        if current != 0:
            # Gate denial is a stop, never a delayed manual mode transition.
            # Do not overwrite the user's release/pause intent or pending work.
            absent = await self._control._async_vehicle_absent()
            await self._control._async_write(CHARGING_RELEASE, 0, refresh=False)
            if not absent:
                self._control.write_state.mode_change_at = self._control._monotonic()
        self.state.hold = True

    async def async_configure(self, enabled: bool, policy: str) -> None:
        if enabled:
            self._control._require_write_access()
        async with self._control.energy_goal_guard:
            self.state.configure(enabled, policy)
            await self.async_sync_locked()

    def check_release(self, *, recovery: bool = False) -> None:
        if self._authorizing is not None:
            self._check_request(*self._authorizing)
        elif (self.state.enabled and not self.state.authorized) or (recovery and self.state.hold):
            raise validation_error("plug_charge_blocked")

    def release_written(self) -> None:
        self.state.hold = False

    def _check_request(self, session: str, deadline: datetime) -> None:
        if self._stopping or not self.state.enabled:
            raise validation_error("plug_charge_blocked")
        if self.state.session != session:
            raise validation_error("plug_charge_session_changed")
        if datetime.now(UTC) >= deadline:
            raise validation_error("plug_charge_authorization_expired")
        if self._control.charging_release_requested is False or self._control.pause_requested is True:
            raise validation_error("plug_charge_blocked")

    async def _live_presence(self, read: LockedRegisterReader) -> None:
        try:
            await self._check_presence(read)
        except AmtronBusError as err:
            self.state.invalidate()
            self._schedule_deny()
            raise validation_error("control_state_unknown") from err

    async def _check_presence(self, read: LockedRegisterReader) -> None:
        evse = await read(EVSE_STATE)
        cp_supported = self._control._identity.layout_version >= CP_STATE.min_layout
        cp = await read(CP_STATE) if cp_supported else None
        present = vehicle_connected(evse, cp, cp_supported=cp_supported)
        if present is not True:
            self.state.observe(present)
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
        if not isinstance(session, str) or _SESSION.fullmatch(session) is None:
            raise validation_error("plug_charge_session_changed")
        if (
            not isinstance(deadline, datetime) or deadline.tzinfo is None or deadline.utcoffset() is None
            or not 0 < (deadline - datetime.now(UTC)).total_seconds() <= 30
        ):
            raise validation_error("plug_charge_authorization_expired")
        async with self._control.energy_goal_guard:
            self._check_request(session, deadline)
            if self._control.energy_goal_stop_requested:
                raise validation_error("energy_goal_stop_active")
            await self._live_presence(self._control._client.async_read_register)
            self._check_request(session, deadline)
            current = await self._control._async_read_switch(CHARGING_RELEASE)
            if current != 1:
                try:
                    await self._control._async_check_transition_interval()
                except AmtronRateLimitedError as err:
                    raise validation_error("write_rate_limited", {"seconds": f"{err.retry_after:.0f}"}) from err
            self._check_request(session, deadline)
            self._authorizing = session, deadline
            try:
                await self._control._async_write(
                    CHARGING_RELEASE, 1, refresh=False, precondition=self._live_presence,
                )
                self._check_request(session, deadline)
                self.state.authorized = True
                self.state.hold = False
                if current != 1:
                    self._control.write_state.mode_change_at = self._control._monotonic()
            except (DeviceError, AmtronBusError, OSError, asyncio.CancelledError) as err:
                if not isinstance(err, DeviceValidationError) and self.state.session == session:
                    self.state.invalidate()
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
