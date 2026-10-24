"""The single write path to the wallbox.

Every state-changing operation goes through this module. That is deliberate:
0x0302 has three different meanings depending on the value written, and two
of them are dangerous.

    0            no limitation -- the wallbox signals its maximum (16/32 A)
    0.01 - 5.99  invalid -- the wallbox signals 0 A, the documented pause
    >= 6         a real charging-current limit

A plain number entity with ``min: 0`` would therefore request full load when
a user drags the slider to zero. The only way to reach either special value
is an explicitly named call below.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from homeassistant.exceptions import HomeAssistantError

from .capabilities import phase_switch_is_supported
from .client import MennekesModbusClient
from .client_errors import AmtronBusError, AmtronRateLimitedError
from .const import (
    CHARGING_CURRENT_MINIMUM,
    CHARGING_CURRENT_UNLIMITED,
    CHARGING_PAUSE_CURRENT,
    HEARTBEAT_VALUE,
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
    MIN_MODE_CHANGE_INTERVAL_SECONDS,
    SYSTEM_RESTART_VALUE,
)
from .data import DeviceIdentity, WallboxData, WriteDiagnostics
from .enums import EvseState, RequestedPhases, SolarChargingMode
from .exceptions import home_assistant_error, service_validation_error
from .registers import (
    CHARGING_CURRENT_EMS,
    CHARGING_RELEASE,
    HEARTBEAT,
    LOCK_EVSE,
    REQUESTED_PHASES,
    SOLAR_CHARGING_MODE,
    SYSTEM_RESTART,
    RegisterSpec,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ControlOptions:
    """User-chosen limits on what the integration may do on the bus."""

    read_only: bool = True
    current_cap: float | None = None


class AmtronControl:
    """Validated, rate-limited write access to one wallbox."""

    def __init__(
        self,
        client: MennekesModbusClient,
        *,
        diagnostics: WriteDiagnostics,
        identity: DeviceIdentity,
        options: ControlOptions,
        state: Callable[[], WallboxData],
        request_refresh: Callable[[], Awaitable[None]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._diagnostics = diagnostics
        self._identity = identity
        self._options = options
        self._state = state
        self._request_refresh = request_refresh
        self._sleep = sleep
        self._monotonic = monotonic
        self._pending_current: float | None = None
        self._current_lock = asyncio.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._mode_change_at: float | None = None
        self.charging_setpoint: float | None = None

    @property
    def options(self) -> ControlOptions:
        """Return the active control options."""

        return self._options

    @property
    def read_only(self) -> bool:
        """Return true while the integration must not write to the bus."""

        return self._options.read_only

    @property
    def max_charging_current(self) -> float:
        """Return the highest charging current a user may request.

        The device limits anything it is sent anyway; this is the limit the
        number entity shows, so a user never sees a value the wallbox would
        silently clamp.
        """

        limits = [
            value
            for value in (
                self._identity.max_evse_current,
                self._options.current_cap,
            )
            if value is not None and value >= CHARGING_CURRENT_MINIMUM
        ]
        if not limits:
            return CHARGING_CURRENT_MINIMUM
        return min(limits)

    @property
    def is_paused(self) -> bool:
        """Return true while the EMS limit holds the documented pause value."""

        value = self._state().get(CHARGING_CURRENT_EMS.key)
        if not isinstance(value, (int, float)):
            return False
        return CHARGING_CURRENT_UNLIMITED < float(value) < CHARGING_CURRENT_MINIMUM

    async def async_set_charging_current(
        self,
        value: float,
        *,
        allow_unlimited: bool = False,
    ) -> None:
        """Set the EMS charging-current limit in 0x0302.

        0 A does not mean "no charging" on this device: it disables the limit
        and makes the EVSE signal its maximum. 0.01-5.99 A is the vendor's
        documented way to pause. Both are therefore reachable only with an
        explicit opt-in or through :meth:`async_pause_charging`.
        """

        self._require_write_access()
        current = float(value)
        if current == CHARGING_CURRENT_UNLIMITED:
            if not allow_unlimited:
                raise service_validation_error("charging_current_zero")
            _LOGGER.warning(
                "Writing 0 A to 0x0302: the wallbox will signal its maximum "
                "current, not a charging stop"
            )
        elif not math.isfinite(current) or current < CHARGING_CURRENT_MINIMUM:
            raise service_validation_error(
                "charging_current_invalid",
                {
                    "value": f"{current:.2f}",
                    "minimum": f"{CHARGING_CURRENT_MINIMUM:.0f}",
                },
            )
        else:
            current = min(current, self.max_charging_current)
            self.charging_setpoint = current
        # Every value this method accepts is either a real limit or the
        # explicit 0 A, and while the pause value is active both of them
        # resume the charge, whatever the caller meant by it. So they answer
        # to the same hysteresis the pause switch does.
        await self._async_request_current(current, mode_change=self.is_paused)

    async def async_pause_charging(self) -> None:
        """Pause charging the documented way, by signalling 0 A to the EV."""

        self._require_write_access()
        value = self._state().get(CHARGING_CURRENT_EMS.key)
        if isinstance(value, (int, float)) and float(value) >= CHARGING_CURRENT_MINIMUM:
            self.charging_setpoint = float(value)
        await self._async_request_current(CHARGING_PAUSE_CURRENT, mode_change=True)

    async def async_resume_charging(self) -> None:
        """Resume charging with the setpoint that was active before the pause."""

        self._require_write_access()
        target = self.charging_setpoint or self.max_charging_current
        await self._async_request_current(
            max(CHARGING_CURRENT_MINIMUM, min(target, self.max_charging_current)),
            mode_change=True,
        )

    async def async_set_charging_release(self, released: bool) -> None:
        """Open or close the charging relay through 0x0D05."""

        self._require_write_access()
        await self._async_write(CHARGING_RELEASE, int(released))

    async def async_set_lock_evse(self, locked: bool) -> None:
        """Lock or unlock the wallbox through 0x0D06."""

        self._require_write_access()
        await self._async_write(LOCK_EVSE, int(locked))

    async def async_set_solar_charging_mode(self, mode: SolarChargingMode) -> None:
        """Set the solar charging mode shown on the wallbox HMI (0x0D03)."""

        self._require_write_access()
        await self._async_write(SOLAR_CHARGING_MODE, int(mode))

    async def async_set_requested_phases(self, phases: RequestedPhases) -> None:
        """Request single-phase or all-phase charging through 0x0D04.

        While the wallbox is charging, the current is set to the documented
        pause value first: the vendor's own sequence does that, and some EVs
        need it before the phase count changes.
        """

        self._require_write_access()
        if not phase_switch_is_supported(self._identity.phase_options_hw):
            raise service_validation_error("phase_switch_unsupported")
        if self._state().get("evse_state") == EvseState.CHARGING:
            await self._async_request_current(CHARGING_PAUSE_CURRENT, mode_change=True)
        await self._async_write(
            REQUESTED_PHASES,
            int(phases),
            min_interval=MIN_MODE_CHANGE_INTERVAL_SECONDS,
        )

    async def async_recover_from_error(self) -> None:
        """Run the documented recovery sequence for error state 200.

        The specification requires 0x0D00, 0x0D05 and 0x0302 to be written,
        in that order, to bring the wallbox back to idle.
        """

        self._require_write_access()
        target = self.charging_setpoint or self.max_charging_current
        async with self._current_lock:
            await self._async_write(HEARTBEAT, HEARTBEAT_VALUE)
            await self._async_write(CHARGING_RELEASE, 1)
            await self._async_request_current_locked(
                max(CHARGING_CURRENT_MINIMUM, min(target, self.max_charging_current)),
                # Recovery only changes charging mode if it lifts a pause.
                # Its final write must finish before recovery reports success.
                mode_change=self.is_paused,
                defer=False,
            )
        self._diagnostics.recoveries += 1

    async def async_restart(self) -> None:
        """Restart the wallbox through 0x0D19, which needs the idle state."""

        self._require_write_access()
        state = self._state().get("evse_state")
        if state != EvseState.IDLE:
            raise service_validation_error("restart_requires_idle")
        await self._async_write(SYSTEM_RESTART, SYSTEM_RESTART_VALUE)

    async def async_shutdown(self) -> None:
        """Cancel a deferred charging-current write during unload."""

        self._pending_current = None
        task = self._flush_task
        self._flush_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def _async_request_current(
        self,
        value: float,
        *,
        mode_change: bool = False,
    ) -> None:
        """Write 0x0302, deferring the write when it comes too fast.

        Two different rules meet on this one register. Every write has to keep
        the vendor's five-second distance; that is a property of the register
        and lives in the client's rate limiter. Pausing and resuming need
        minutes between them; that is a property of the operation and lives
        here. Keying both on the register made a dragged slider block the
        pause switch for five minutes, and let a pause be undone six seconds
        later by a slider move.

        Rejecting a plain setpoint write would make a dragged slider fail, so
        the newest value is remembered and written once the interval passed. A
        mode change is reported back instead: it is a deliberate act, and
        quietly performing it minutes later is worse than refusing it.
        """

        async with self._current_lock:
            await self._async_request_current_locked(
                value, mode_change=mode_change or self.is_paused
            )

    async def _async_request_current_locked(
        self, value: float, *, mode_change: bool, defer: bool = True
    ) -> None:
        """Serialize a new command against any deferred setpoint."""

        if mode_change:
            remaining = self._mode_change_remaining()
            if remaining > 0:
                self._diagnostics.writes_rate_limited += 1
                raise service_validation_error(
                    "write_rate_limited", {"seconds": f"{remaining:.0f}"}
                )
        try:
            await self._async_write(
                CHARGING_CURRENT_EMS,
                value,
                min_interval=MIN_CURRENT_WRITE_INTERVAL_SECONDS,
            )
        except AmtronRateLimitedError as err:
            self._diagnostics.writes_rate_limited += 1
            if mode_change or not defer:
                raise service_validation_error(
                    "write_rate_limited",
                    {"seconds": f"{err.retry_after:.0f}"},
                ) from err
            self._pending_current = value
            self._ensure_flush_task()
            return
        # A successful command supersedes every older deferred setpoint,
        # including one whose background task is currently sleeping.
        self._pending_current = None
        if mode_change:
            self._mode_change_at = self._monotonic()

    def _mode_change_remaining(self) -> float:
        """Return the seconds left of the pause and resume hysteresis."""

        if self._mode_change_at is None:
            return 0.0
        waited = self._monotonic() - self._mode_change_at
        return max(0.0, MIN_MODE_CHANGE_INTERVAL_SECONDS - waited)

    def _ensure_flush_task(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.get_running_loop().create_task(
            self._async_flush_pending_current(),
            name="mennekes_amtron_rtu_current_flush",
        )

    async def _async_flush_pending_current(self) -> None:
        try:
            while True:
                async with self._current_lock:
                    value = self._pending_current
                    if value is None:
                        return
                    try:
                        await self._async_write(
                            CHARGING_CURRENT_EMS,
                            value,
                            min_interval=MIN_CURRENT_WRITE_INTERVAL_SECONDS,
                        )
                    except AmtronRateLimitedError as err:
                        retry_after = err.retry_after
                    except HomeAssistantError:
                        # The write path already counted and translated it.
                        self._pending_current = None
                        return
                    else:
                        self._pending_current = None
                        return
                # New commands must be able to replace a sleeping setpoint.
                await self._sleep(retry_after)
        finally:
            self._flush_task = None

    async def _async_write(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
    ) -> None:
        try:
            await self._client.async_write_register(
                spec, value, min_interval=min_interval
            )
        except AmtronRateLimitedError:
            raise
        except AmtronBusError as err:
            self._diagnostics.writes_rejected += 1
            self._diagnostics.last_error = str(err)
            raise home_assistant_error(
                "write_failed", {"register": spec.key, "error": str(err)}
            ) from err
        self._diagnostics.writes_sent += 1
        if self._request_refresh is not None:
            await self._request_refresh()

    def _require_write_access(self) -> None:
        if self._options.read_only:
            raise service_validation_error("read_only_mode")
