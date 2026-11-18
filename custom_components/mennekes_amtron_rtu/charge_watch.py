"""Name the vehicle of a plug cycle the service measured.

The service keeps the meter log and sees each plug cycle from plug-in to
unplug. Home Assistant adds what only it knows: the vehicle profile
recognized for that whole cycle. It hands that to the service as a note on
the running cycle, so the name stays with the cycle through a restart of
either side. This is evidence for an estimate, never a charging permission.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .device.client_errors import AmtronConnectionError
from .device.errors import DeviceError
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    VehicleProfile,
    active_profile,
    profiles,
    vehicle_options,
)

# A second name for one cycle makes it nobody's.
NOBODY = "x"
# The car's clock and the service's may differ by this much.
CLOCK_SLACK_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class PlugCycle:
    """A plug cycle seen from plug-in to unplug, and whose car it was."""

    plugged_at: float
    unplugged_at: float
    owner: VehicleProfile | None


def note_for(profile: VehicleProfile, *, automatic: bool) -> str:
    """The profile as it is now: an edited profile is no longer the same car."""
    digest = hashlib.sha256(
        json.dumps(profile.as_dict(), sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return f"{'a' if automatic else 'm'}:{digest}:{profile.id}"


def _named(options: Mapping[str, Any], note: str | None) -> tuple[VehicleProfile, bool] | None:
    """The profile a note names and whether it was recognized automatically."""
    if note is None or note == NOBODY:
        return None
    kind, _, rest = note.partition(":")
    profile = profiles(options).get(rest.partition(":")[2])
    if profile is None or kind not in ("a", "m") or note_for(profile, automatic=kind == "a") != note:
        return None
    return profile, kind == "a"


def _claim(options: Mapping[str, Any], meter: Mapping[str, Any], note: str | None,
           profile: VehicleProfile, *, automatic: bool) -> str | None:
    """The note after naming the car of the running cycle."""
    if note == NOBODY:
        return note
    if note is not None:
        return note if note.partition(":")[2] == note_for(profile, automatic=automatic).partition(":")[2] else NOBODY
    charging_at = meter["charging_at"]
    if not automatic and charging_at is not None:
        # A car selected by hand owns only a charge that began afterwards.
        selected_at = options.get(CONF_VEHICLE_PROFILE_SELECTED_AT)
        if (
            not isinstance(selected_at, (int, float)) or isinstance(selected_at, bool)
            or selected_at > charging_at
        ):
            return None
    return note_for(profile, automatic=automatic)


def _settled(options: Mapping[str, Any], meter: Mapping[str, Any], note: str | None) -> str | None:
    """The note the running cycle should carry under the current selection."""
    selected = active_profile(options)
    automatic = options.get(CONF_VEHICLE_AUTO_SELECTION) is True
    if note is not None and note != NOBODY:
        named = _named(options, note)
        if (
            named is None
            or (selected is not None and selected.id != named[0].id)
            or (selected is None and not automatic)
        ):
            note = NOBODY
    if not automatic and selected is not None:
        note = _claim(options, meter, note, selected, automatic=False)
    return note


def _meter(entry: MennekesAmtronConfigEntry) -> dict[str, Any] | None:
    runtime = getattr(entry, "runtime_data", None)
    return runtime.coordinator.current_data().charge_meter if runtime is not None else None


def last_cycle(entry: MennekesAmtronConfigEntry) -> PlugCycle | None:
    """The last plug cycle the service saw from end to end."""
    meter = _meter(entry)
    if meter is None or meter["last"] is None:
        return None
    named = _named(entry.options, meter["last_note"])
    start, end = meter["last"]
    return PlugCycle(start, end, named[0] if named is not None else None)


def cycle_around(entry: MennekesAmtronConfigEntry, start: float, end: float) -> PlugCycle | None:
    """The plug cycle a charging span of the car lies in; None when it was not here.

    A car's own record says when it charged, not where. Only a span inside a
    cycle the service saw from the plug-in on was charged at this wallbox.
    """
    meter = _meter(entry)
    if meter is None:
        return None
    plugged_at, last = meter["plugged_at"], meter["last"]
    if plugged_at is not None and plugged_at <= start + CLOCK_SLACK_SECONDS:
        named = _named(entry.options, meter["note"])
        return PlugCycle(plugged_at, end, named[0] if named is not None else None)
    if last is not None and last[0] <= start + CLOCK_SLACK_SECONDS and end <= last[1] + CLOCK_SLACK_SECONDS:
        return last_cycle(entry)
    return None


def measurable_since(entry: MennekesAmtronConfigEntry) -> float | None:
    """From when a charging record of the car can lie in a plug cycle seen here."""
    meter = _meter(entry)
    if meter is None:
        return None
    starts = [at for at in (meter["plugged_at"], meter["last"][0] if meter["last"] is not None else None) if at is not None]
    return min(starts) - CLOCK_SLACK_SECONDS if starts else None


def owner(entry: MennekesAmtronConfigEntry) -> VehicleProfile | None:
    """The recognized car of the running cycle, else of the last one."""
    meter = _meter(entry)
    if meter is None:
        return None
    if meter["plugged_at"] is not None:
        named = _named(entry.options, meter["note"])
        return named[0] if named is not None and named[1] else None
    named = _named(entry.options, meter["last_note"])
    return named[0] if named is not None else None


def loss_vehicle_options(entry: MennekesAmtronConfigEntry) -> dict[str, Any]:
    """Retain only loss settings for the recognized owner after auto deselection."""
    if (
        entry.options.get(CONF_VEHICLE_AUTO_SELECTION) is True
        and active_profile(entry.options) is None
        and (profile := owner(entry)) is not None
    ):
        return vehicle_options({**entry.options, CONF_ACTIVE_VEHICLE_PROFILE: profile.id})
    return vehicle_options(entry.options)


class ChargeWatch:
    """Keeps the service's note on the running cycle in step with the selection."""

    def __init__(self, hass: HomeAssistant, entry: MennekesAmtronConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        # The note the running cycle should carry until the service reports it.
        self._wanted: tuple[float, str] | None = None
        self._sending = False

    def start(self) -> CALLBACK_TYPE:
        """Follow every poll of this entry."""
        unsubscribe = self._entry.runtime_data.coordinator.async_add_listener(self.observe)
        self.observe()
        return unsubscribe

    def _note(self, meter: Mapping[str, Any]) -> str | None:
        wanted = self._wanted
        if wanted is not None and wanted[0] != meter["plugged_at"]:
            wanted = self._wanted = None
        return wanted[1] if wanted is not None else meter["note"]

    @callback
    def observe(self) -> None:
        meter = _meter(self._entry)
        if meter is None or meter["plugged_at"] is None:
            # Not polled yet, a failed poll, or no car: nothing to name.
            return
        self._want(meter, _settled(self._entry.options, meter, self._note(meter)))

    @callback
    def confirm_automatic(self, profile_id: str) -> None:
        """A car was recognized on the running cycle."""
        meter = _meter(self._entry)
        profile = profiles(self._entry.options).get(profile_id)
        if meter is None or meter["plugged_at"] is None or profile is None:
            return
        note = _settled(self._entry.options, meter, self._note(meter))
        self._want(meter, _claim(self._entry.options, meter, note, profile, automatic=True))

    def _want(self, meter: Mapping[str, Any], note: str | None) -> None:
        if note is None or note == meter["note"]:
            self._wanted = None
            return
        self._wanted = wanted = (meter["plugged_at"], note)
        if not self._sending:
            self._sending = True
            self._entry.async_create_background_task(
                self._hass, self._send(*wanted), "amtron-charge-note",
            )

    async def _send(self, plugged_at: float, note: str) -> None:
        try:
            await self._entry.runtime_data.client.execute(
                "charge_note", plugged_at=plugged_at, note=note,
            )
        except (AmtronConnectionError, DeviceError):
            # The next poll tries again while the cycle still runs.
            pass
        finally:
            self._sending = False


def confirm_automatic_vehicle(entry: MennekesAmtronConfigEntry, profile_id: str) -> None:
    runtime = getattr(entry, "runtime_data", None)
    watch = runtime.charge_watch if runtime is not None else None
    if watch is not None:
        watch.confirm_automatic(profile_id)
