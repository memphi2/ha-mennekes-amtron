"""Switch the charging release off when the car is unplugged.

The whole rule lives here, in the service that owns the bus: it sees the
unplug, remembers it in the saved intent and writes the register itself. It
therefore works while Home Assistant is away, needs no queue and no timer of
its own, and a restart finds an unplug that happened meanwhile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from .client_errors import AmtronBusError
from .errors import DeviceError
from .notice import FailureNotice
from .registers import CP_STATE, EVSE_STATE
from .vehicle import vehicle_connected

if TYPE_CHECKING:
    from collections.abc import Callable

    from .control import AmtronControl
    from .data import WallboxData

_LOGGER = logging.getLogger(__name__)
_RETRY_SECONDS = 30


@dataclass(slots=True)
class UnplugReleaseState:
    """What the rule knows; saved with the control intent."""

    enabled: bool = False
    # The last plug state the wallbox confirmed. Unknown never counts.
    plugged: bool | None = None
    # The car left and the release is still to be switched off.
    owed: bool = False

    def configure(self, enabled: object) -> None:
        if type(enabled) is not bool:
            raise ValueError("invalid unplug rule configuration")
        self.enabled = enabled
        if not enabled:
            self.owed = False

    def observe(self, present: bool | None) -> None:
        if present is None:
            return
        if present:
            # The car is back: its release is not the rule's to close.
            self.owed = False
        elif self.plugged is True and self.enabled:
            self.owed = True
        self.plugged = present

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "plugged": self.plugged, "owed": self.owed}

    @classmethod
    def restore(cls, raw: object) -> Self:
        if (
            not isinstance(raw, dict) or raw.keys() != {"enabled", "plugged", "owed"}
            or type(raw["enabled"]) is not bool or type(raw["owed"]) is not bool
            or raw["plugged"] not in (None, True, False)
        ):
            raise ValueError("invalid saved unplug rule")
        return cls(raw["enabled"], raw["plugged"], raw["owed"] and raw["enabled"])


class UnplugRelease:
    """Observe every poll; close the release once the wallbox confirms the car is gone."""

    def __init__(self, control: AmtronControl, monotonic: Callable[[], float]) -> None:
        self._control = control
        self._monotonic = monotonic
        self._retry_at = 0.0
        self._notice = FailureNotice(_LOGGER, "Control")

    @property
    def state(self) -> UnplugReleaseState:
        return self._control.write_state.unplug_release

    def observe(self, data: WallboxData) -> None:
        # Only values read in this poll decide: a held one is no evidence.
        self.state.observe(vehicle_connected(
            data.fresh(EVSE_STATE.key), data.fresh(CP_STATE.key),
            cp_supported=self._control.layout_version >= CP_STATE.min_layout,
        ))

    async def async_apply(self) -> None:
        """Switch off what is owed; the caller owns the command lock."""
        control = self._control
        state = self.state
        if not state.owed or control.read_only or self._monotonic() < self._retry_at:
            return
        if control.write_state.plug_charge.enabled:
            # The gate owns the release and keeps it closed without a car.
            state.owed = False
            return
        try:
            closed = await self._async_close()
            if closed and control.pause_requested is True and not control.energy_goal_stop_requested:
                # Without a car and with the release off, ending the pause
                # lets no current flow; the next release starts at once.
                await control.async_resume_charging()
        except (DeviceError, AmtronBusError, OSError) as err:
            self._retry_at = self._monotonic() + _RETRY_SECONDS
            self._notice.failed(
                "charging release not switched off after unplugging;"
                f" retrying every {_RETRY_SECONDS} seconds",
                err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
            )
            return
        if closed:
            state.owed = False
            self._notice.recovered("charging release switched off after unplugging")

    async def _async_close(self) -> bool:
        """Write the release off; False while the wallbox does not confirm the car is gone."""
        control = self._control
        async with control.energy_goal_guard:
            if not await control.async_unplugged():
                # Unknown, or plugged in again: the next poll decides.
                return False
            await control.async_close_release("unplug")
            return True
