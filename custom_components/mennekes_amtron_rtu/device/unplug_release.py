"""What an unplug ends: the charging release, if so configured, and a reached target.

Both rules live here, in the service that owns the bus: it sees the
unplug, remembers it in the saved intent and writes the register itself. It
therefore works while Home Assistant is away, needs no queue and no timer of
its own, and a restart finds an unplug that happened meanwhile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from .client_errors import AmtronBusError, AmtronRateLimitedError
from .errors import DeviceError
from .notice import FailureNotice

if TYPE_CHECKING:
    from .control import AmtronControl

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
    # The car left with an energy or cost target set: "goal". Reached, now or
    # by a meter reading that only comes after the unplug, the target was
    # that charge's and is to be ended with the pause that is on then;
    # "pause" is what is left once the target is cleared. Whose pause it is
    # is not asked: without a car ending one lets no current flow, while one
    # left behind keeps the next car from charging.
    goal_owed: str | None = None

    def configure(self, enabled: object) -> None:
        if type(enabled) is not bool:
            raise ValueError("invalid unplug rule configuration")
        self.enabled = enabled
        if not enabled:
            self.owed = False

    def observe(self, present: bool | None, *, armed: bool = False) -> None:
        if present is None:
            return
        if present:
            # The car is back: its release is not the rule's to close, and a
            # target reached from now on is this charge's.
            self.owed = False
            self.goal_owed = None
        elif self.plugged is True:
            self.owed = self.enabled
            self.goal_owed = "goal" if armed else None
        self.plugged = present

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled, "plugged": self.plugged, "owed": self.owed, "goal_owed": self.goal_owed,
        }

    @classmethod
    def restore(cls, raw: object) -> Self:
        if (
            not isinstance(raw, dict) or raw.keys() != {"enabled", "plugged", "owed", "goal_owed"}
            or type(raw["enabled"]) is not bool or type(raw["owed"]) is not bool
            or raw["goal_owed"] not in (None, "goal", "pause")
            or raw["plugged"] not in (None, True, False)
        ):
            raise ValueError("invalid saved unplug rule")
        return cls(raw["enabled"], raw["plugged"], raw["owed"] and raw["enabled"], raw["goal_owed"])


class UnplugRelease:
    """Observe every poll; close the release once the wallbox confirms the car is gone."""

    def __init__(self, control: AmtronControl) -> None:
        self._control = control
        self._retry_at = 0.0
        self._notice = FailureNotice(_LOGGER, "Control")
        self._goal_retry_at = 0.0
        self._goal_notice = FailureNotice(_LOGGER, "Energy target")

    @property
    def state(self) -> UnplugReleaseState:
        return self._control.write_state.unplug_release

    def follow(self) -> None:
        """After the plug cycle took a poll."""
        control = self._control
        self.state.observe(control.write_state.plug_cycle.present, armed=control.energy_goal.target_kwh > 0)

    async def async_end_reached_goal(self) -> None:
        """End a reached target of the car that left; the caller owns the command lock.

        The next car starts without the previous one's limit and without the
        pause that limit caused. Nothing is written while the wallbox does not
        confirm that no car is there, and what is owed stays owed until it is
        done or a car is back.
        """
        control = self._control
        state = self.state
        owed = state.goal_owed
        if owed is None:
            # Nothing left to do, however it ended: a later failure is a new one.
            self._goal_retry_at = 0.0
            self._goal_notice.recovered("no reached target left to end after unplugging")
            return
        if control.read_only or control.monotonic() < self._goal_retry_at:
            return
        goal = control.energy_goal
        if owed == "goal":
            if not goal.target_kwh:
                # Cleared by hand meanwhile: nothing of the rule's is left,
                # and a pause set since is not its to end.
                state.goal_owed = None
                return
            if goal.status != "reached":
                # Not reached: it waits for the next plug-in, or for the
                # meter reading that shows this charge reached it.
                return
        try:
            async with control.energy_goal_guard:
                absent = await control.async_unplugged()
            if not absent:
                # Unknown, or plugged in again: the next poll decides.
                return
            if owed == "goal":
                try:
                    await control.async_set_energy_goal(0)
                finally:
                    # Read anew: setting a target replaces the model.
                    if not control.energy_goal.target_kwh:
                        # Also when it failed after it took effect: the
                        # pause is still to end.
                        state.goal_owed = "pause"
            if control.pause_requested is True:
                if control.energy_goal_stop_requested or control.pending_action is not None:
                    # Another limit holds the pause, or its end already waits.
                    return
                await control.async_resume_charging()
                if control.pause_requested is True:
                    # Queued behind the floor between two current writes.
                    return
        except AmtronRateLimitedError as err:
            self._goal_retry_at = control.monotonic() + err.retry_after
            return
        except (DeviceError, AmtronBusError, OSError) as err:
            self._goal_retry_at = control.monotonic() + _RETRY_SECONDS
            self._goal_notice.failed(
                f"reached target not ended after unplugging; retrying every {_RETRY_SECONDS} seconds",
                err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
            )
            return
        state.goal_owed = None
        self._goal_retry_at = 0.0
        if not self._goal_notice.recovered("reached target ended after unplugging"):
            _LOGGER.info("Energy target: reached target ended after unplugging")

    async def async_apply(self) -> None:
        """Switch off what is owed; the caller owns the command lock."""
        control = self._control
        state = self.state
        if control.write_state.plug_charge.enabled:
            # The gate owns the release and keeps it closed without a car.
            state.owed = False
        if not state.owed:
            # Nothing left to do, however it ended: a later failure is a new one.
            self._retry_at = 0.0
            self._notice.recovered("no charging release left to switch off after unplugging")
            return
        if control.read_only or control.monotonic() < self._retry_at:
            return
        try:
            closed = await self._async_close()
        except AmtronRateLimitedError as err:
            # The state-change interval of a wallbox that cannot prove the
            # car is gone: no failure, only later.
            self._retry_at = control.monotonic() + err.retry_after
            return
        except (DeviceError, AmtronBusError, OSError) as err:
            self._retry_at = control.monotonic() + _RETRY_SECONDS
            self._notice.failed(
                "charging release not switched off after unplugging;"
                f" retrying every {_RETRY_SECONDS} seconds",
                err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
            )
            return
        if not closed:
            return
        state.owed = False
        self._notice.recovered("charging release switched off after unplugging")
        if control.pause_requested is True and not control.energy_goal_stop_requested:
            # Without a car and with the release off, ending the pause lets
            # no current flow; the next release starts at once.
            try:
                await control.async_resume_charging()
            except (DeviceError, AmtronBusError, OSError) as err:
                # The release is off, which is what the rule owes. The pause
                # stays and its switch shows it.
                _LOGGER.warning(
                    "Control: pause not ended after unplugging (%s)",
                    err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
                )

    async def _async_close(self) -> bool:
        """Write the release off; False while the wallbox does not confirm the car is gone."""
        control = self._control
        async with control.energy_goal_guard:
            if not await control.async_unplugged():
                # Unknown, or plugged in again: the next poll decides.
                return False
            await control.async_close_release("unplug")
            return True
