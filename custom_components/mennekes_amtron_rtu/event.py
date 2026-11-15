"""One activity entity: what was done to the wallbox and what changed at it.

Six event types answer "what kind of thing happened"; the attributes say
which one. An automation triggers on the type and narrows by attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.components.logbook import async_log_entry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util

from .data import WallboxData
from .device.command_events import CommandEvent
from .device.const import CHARGING_CURRENT_DECIMALS, DOMAIN
from .device.registers import CHARGING_CURRENT_EMS, CP_STATE
from .device.wallbox_events import WallboxChanges
from .entity import AmtronEntity, Projection
from .entry_types import MennekesAmtronConfigEntry

PARALLEL_UPDATES = 0

COMMAND_DONE = "command_done"
COMMAND_FAILED = "command_failed"
COMMAND_CANCELLED = "command_cancelled"
SESSION_STARTED = "session_started"
SESSION_ENDED = "session_ended"
WALLBOX_CHANGED = "wallbox_changed"
EVENT_TYPES = (
    COMMAND_DONE, COMMAND_FAILED, COMMAND_CANCELLED,
    SESSION_STARTED, SESSION_ENDED, WALLBOX_CHANGED,
)

_COMMAND_TYPES = {
    "executed": COMMAND_DONE, "recovery_succeeded": COMMAND_DONE,
    "failed": COMMAND_FAILED, "uncertain": COMMAND_FAILED, "recovery_failed": COMMAND_FAILED,
    "cancelled": COMMAND_CANCELLED, "replaced": COMMAND_CANCELLED,
}
# A car arriving or leaving frames a session; everything else is a change.
_SESSION_TYPES = {"vehicle_plugged_in": SESSION_STARTED, "vehicle_unplugged": SESSION_ENDED}


@dataclass(frozen=True, kw_only=True)
class _Description(Projection, EventEntityDescription):
    pass


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MennekesAmtronConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([AmtronActivity(entry, _Description(key="activity"))])


def _current_readback(reading: WallboxData | None) -> float | None:
    value = (
        reading.fresh(CHARGING_CURRENT_EMS.key)
        if reading is not None and "current_limits" not in reading.failed_blocks else None
    )
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 32:
        return round(value, CHARGING_CURRENT_DECIMALS)
    return None


class AmtronActivity(AmtronEntity, EventEntity):
    """Commands the service carried out, and changes the wallbox reported."""

    _attr_event_types = list(EVENT_TYPES)  # noqa: RUF012 - HA declares it per instance

    def __init__(self, entry: MennekesAmtronConfigEntry, description: _Description) -> None:
        super().__init__(entry, description)
        self._feed = entry.runtime_data.control.events
        self._labels: dict[str, str] = {}
        # The last successful setpoint, so a periodic automation that repeats
        # it reports once.
        self._last_setpoint: tuple[str, float, str] | None = None
        self._last_current: float | None = None
        self._changes = WallboxChanges(
            cp_supported=entry.runtime_data.identity.layout_version >= CP_STATE.min_layout,
        )

    @property
    def available(self) -> bool:
        # Events are moments, not a state that goes stale with the connection.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The reading present when the entity appears is the baseline, so a
        # reload reports nothing that happened before it.
        reading = self._reading()
        self._changes.observe(reading)
        self._last_current = _current_readback(reading)
        self._labels = await async_get_translations(
            self.hass, self.hass.config.language, "entity", {DOMAIN},
        )
        self.async_on_remove(self._feed.listen(self._command))

    def _reading(self) -> WallboxData | None:
        return self.coordinator.data if self.coordinator.last_update_success else None

    @callback
    def _handle_coordinator_update(self) -> None:
        reading = self._reading()
        changes = self._changes.observe(reading)
        current = _current_readback(reading)
        if changes or current is None or (
            self._last_setpoint is not None and current != self._last_current
            and current != round(self._last_setpoint[1], CHARGING_CURRENT_DECIMALS)
        ):
            # Something happened in between: the same setpoint is news again.
            self._last_setpoint = None
        self._last_current = current
        for change in changes:
            self._trigger_event(
                _SESSION_TYPES.get(change.type, WALLBOX_CHANGED),
                {"change": change.type, **change.attributes},
            )
            self.async_write_ha_state()

    def _repeats(self, event: CommandEvent) -> bool:
        value = event.requested_value
        key = (
            (event.source, float(value), event.reason)
            if event.action == "set_current" and event.outcome == "executed"
            and event.reason in ("completed", "vehicle_authorized")
            and isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )
        previous, self._last_setpoint = self._last_setpoint, key
        return key is not None and key == previous

    @callback
    def _command(self, event: CommandEvent) -> None:
        # Dragging the current slider supersedes many unsent values; only the
        # final outcome is activity.
        if self._repeats(event) or (event.action == "set_current" and event.outcome == "replaced"):
            return
        kind = _COMMAND_TYPES[event.outcome]
        self._trigger_event(kind, {
            "action": event.action, "result": event.outcome, "reason": event.reason,
            "trigger": event.source, "value": event.requested_value,
            "occurred_at": event.occurred_at,
        })
        self.async_write_ha_state()
        async_log_entry(
            self.hass, "Mennekes Amtron", self._sentence(kind, event),
            domain=DOMAIN, entity_id=self.entity_id,
        )

    def _label(self, attribute: str, value: str) -> str:
        key = f"component.{DOMAIN}.entity.event.activity.state_attributes.{attribute}.state.{value}"
        return self._labels.get(key, value)

    def _name(self, attribute: str, fallback: str) -> str:
        key = f"component.{DOMAIN}.entity.event.activity.state_attributes.{attribute}.name"
        return self._labels.get(key, fallback)

    def _sentence(self, kind: str, event: CommandEvent) -> str:
        """Who asked for what, how it ended and why: two translations, composed."""
        text = f"{self._label('trigger', event.source)}: {self._label('action', event.action)}"
        value = event.requested_value
        if value is not None and event.action in ("set_current", "resume"):
            text += f" ({self._label('value', '0')})" if value == 0 else f" ({value:g} A)"
        text += f" - {self._label('event_type', kind)}"
        if event.reason != "completed":
            text += f". {self._label('reason', event.reason)}"
        text += "."
        if event.occurred_at is not None:
            # The logbook row is written now; the text keeps the service's
            # own time, with the offset for a repeated hour at a clock change.
            at = dt_util.as_local(datetime.fromisoformat(event.occurred_at))
            text += f" {self._name('occurred_at', 'Occurred at')}: {at.isoformat(sep=' ', timespec='seconds')}."
        return text
