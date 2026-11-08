"""Which vehicle entities an entry has, independent of the selected car.

A vehicle switch is applied without reloading the entry, so entities exist as
soon as any profile uses their feature. They are unavailable while the
selected car does not use it.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_SENSOR,
)
from .entry_types import MennekesAmtronConfigEntry
from .timed_goal_const import CONF_TIMED_CALENDAR_ENTITY, timed_goal_is_enabled
from .vehicle_profiles import each_vehicle_options, vehicle_soc_is_configured
from .vehicle_sources import resolve_departure_source

SOC = "soc"
TIMED = "timed"
CALENDAR = "calendar"
VEHICLE_DEPARTURE = "vehicle_departure"
LOSS = "loss"
LOSS_ASSIGN = "loss_assign"


def vehicle_features(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry,
) -> frozenset[str]:
    """Return the features any configured vehicle uses."""
    features: set[str] = set()
    for options in each_vehicle_options(entry.options):
        loss = options.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED)
        if loss != LOSS_SOURCE_DISABLED:
            features.add(LOSS)
        if loss == LOSS_SOURCE_SENSOR:
            features.add(LOSS_ASSIGN)
        if not vehicle_soc_is_configured(options):
            continue
        features.add(SOC)
        if not timed_goal_is_enabled(options):
            continue
        features.add(TIMED)
        if options.get(CONF_TIMED_CALENDAR_ENTITY):
            features.add(CALENDAR)
        if resolve_departure_source(hass, options) is not None:
            features.add(VEHICLE_DEPARTURE)
    return frozenset(features)
