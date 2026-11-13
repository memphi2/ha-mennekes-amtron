"""Service-only policy extension of the single existing write path.

This owner deliberately extends the control's internal pre-write hooks; no
second Modbus writer, alternate queue or direct-mode behavior is introduced.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .client import LockedRegisterReader
from .control import AmtronControl, _PendingCommand, _TimedCommandCancelledError
from .errors import validation_error
from .registers import (
    CHARGING_RELEASE,
    CP_STATE,
    ERROR_CODE,
    EVSE_STATE,
    LOCK_EVSE,
    RegisterSpec,
)
from .vehicle import vehicle_connected


class JobAmtronControl(AmtronControl):
    """Fence in-flight starts using synchronous local-owner evidence."""

    _service_policy: Callable[[str | None, bool], bool | None] | None = None
    _service_stop: Callable[[], bool] | None = None

    def configure_service_policy(
        self, policy: Callable[[str | None, bool], bool | None],
        stop: Callable[[], bool],
    ) -> None:
        self._service_policy, self._service_stop = policy, stop

    @property
    def energy_goal_stop_requested(self) -> bool:
        return super().energy_goal_stop_requested or (self._service_stop is not None and self._service_stop())

    def _check_write_allowed(self, spec: RegisterSpec, value: float, *, recovery_release: bool) -> None:
        if self._write_enables_charging(spec, value) and self._service_policy is not None:
            owner = self._active_command.owner if self._active_command is not None else None
            if self._service_policy(owner, True) is False:
                raise validation_error("energy_goal_stop_active")
        super()._check_write_allowed(spec, value, recovery_release=recovery_release)

    def _timed_cancellation_reason(self, command: _PendingCommand) -> str | None:
        if command.owner is not None and self._service_policy is not None:
            permission = self._service_policy(command.owner, command.action != "pause")
            if permission is not None:
                if command.deadline is not None and datetime.now(UTC) >= command.deadline:
                    return "deadline_expired"
                return None if permission and not self.energy_goal_stop_requested else "energy_goal"
        return super()._timed_cancellation_reason(command)

    async def _async_check_timed_vehicle(self, read: LockedRegisterReader) -> None:
        command = self._active_command
        if (
            command is not None and command.owner is not None
            and self._service_policy is not None
            and self._service_policy(command.owner, command.action != "pause") is True
        ):
            evse = await read(EVSE_STATE)
            cp = await read(CP_STATE) if self._identity.layout_version >= CP_STATE.min_layout else None
            if vehicle_connected(evse, cp, cp_supported=self._identity.layout_version >= CP_STATE.min_layout) is not True:
                raise _TimedCommandCancelledError("unknown")
            for register, expected in ((ERROR_CODE, 0), (LOCK_EVSE, 0), (CHARGING_RELEASE, 1)):
                if await read(register) != expected:
                    raise _TimedCommandCancelledError("unknown")
            return
        await super()._async_check_timed_vehicle(read)
