"""Which car's charge level counts, and the vehicle charge target chosen for it."""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Any, Final

from .connection_sample import connection_sample
from .errors import DeviceError
from .service_config import number

if TYPE_CHECKING:
    from .control import AmtronControl
    from .service_runtime import ModbusService

# A charge level Home Assistant reported counts for the app this long.
# How long the app counts a reported charge level as known.
READING_SECONDS: Final = 900.0


class SocChoice:
    """The vehicle source and its last charge level, as a part of the one service.

    Home Assistant names the car and reports its level; the app only chooses
    a target for the car named. ``revision`` changes with every change of the
    source or the target, so a choice made on an older page is refused.
    """

    def __init__(self, service: ModbusService) -> None:
        self._service = service
        self.revision = secrets.token_hex(16)
        # Whether the car must report its own limit before a target counts.
        self.maximum_required: bool | None = None
        # Source, level, when it was reported, and for which connection.
        self._reading: tuple[str, float | None, float, str] | None = None

    def _scope(self) -> str | None:
        sample = connection_sample(self._service)
        return sample.scope if sample.available and sample.present is True else None

    def follow(self) -> None:
        """After every poll: a level reported for a car that may be gone is none."""
        if self._scope() is None:
            self._reading = None

    async def configure(self, control: AmtronControl, payload: dict[str, Any]) -> None:
        """The soc_source command."""
        source = payload.get("source")
        if source is not None and not isinstance(source, str):
            raise ValueError("source must be a string or null")
        maximum_required = payload.get("maximum_required")
        if maximum_required is not None and type(maximum_required) is not bool:
            raise ValueError("maximum_required must be boolean")
        before = control.soc_goal.snapshot()
        success = False
        try:
            await control.async_configure_soc(source)
            self.maximum_required = maximum_required
            if before["source"] != source:
                self._reading = None
            success = True
        finally:
            if success or control.soc_goal.snapshot() != before:
                self.revision = secrets.token_hex(16)

    async def apply(self, control: AmtronControl, command: str, payload: dict[str, Any]) -> None:
        """The soc_goal, soc_observation and soc_goal_choice commands."""
        value = payload.get("value")
        if command == "soc_goal_choice":
            if "expected_choice_revision" not in payload or self.maximum_required is None:
                raise DeviceError("soc_goal_source_changed")
            if value != 0 and self.maximum_required and control.soc_goal.maximum_percent is None:
                raise DeviceError("soc_goal_limit_unavailable")
            cap = control.soc_goal.maximum_percent
            if cap is not None and isinstance(value, (int, float)) and value > cap:
                # Typed into the app: said, not pressed down in silence.
                raise DeviceError("soc_goal_invalid")
            if cap is not None and value == cap:
                # The top of the app's scale: the car's limit, wherever it goes.
                value = 100
            reading = self._reading
            known = (
                reading is not None and reading[0] == control.soc_goal.source and reading[3] == self._scope()
                and time.monotonic() - reading[2] <= READING_SECONDS
            )
            payload = {
                **payload, "source": control.soc_goal.source, "maximum": control.soc_goal.maximum_percent,
                "soc": reading[1] if reading is not None and known else None,
            }
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("source is required")
        soc = None if payload.get("soc") is None else number(payload["soc"], 0, 100)
        maximum = None if payload.get("maximum") is None else number(payload["maximum"], 0, 100)
        if command == "soc_observation":
            await control.async_observe_soc(soc, maximum, source=source)
        else:
            before = control.soc_goal.snapshot()
            success = False
            try:
                await control.async_set_soc_goal(number(value, 0, 100), source=source, soc=soc, maximum=maximum)
                success = True
            finally:
                if success or control.soc_goal.snapshot() != before:
                    self.revision = secrets.token_hex(16)
        if command != "soc_goal_choice":
            scope = self._scope()
            self._reading = None if scope is None else (source, soc, time.monotonic(), scope)
