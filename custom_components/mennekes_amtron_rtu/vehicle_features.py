"""Which vehicle entities an entry has, independent of the selected car.

A vehicle switch is applied without reloading the entry, so entities exist as
soon as any profile uses their feature. They are unavailable while the
selected car does not use it.
"""

from __future__ import annotations

from .charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    LOSS_SOURCE_DISABLED,
)
from .entry_types import MennekesAmtronConfigEntry
from .vehicle_profiles import each_vehicle_options, vehicle_soc_is_configured

SOC = "soc"
LOSS = "loss"


def vehicle_features(entry: MennekesAmtronConfigEntry) -> frozenset[str]:
    """Return the features any configured vehicle uses."""
    features: set[str] = set()
    for options in each_vehicle_options(entry.options):
        loss = options.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED)
        if loss != LOSS_SOURCE_DISABLED:
            features.add(LOSS)
        if vehicle_soc_is_configured(options):
            features.add(SOC)
    return frozenset(features)
