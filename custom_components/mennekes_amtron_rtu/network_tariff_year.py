"""Refresh an opted-in German Module 3 estimate at the tariff-year boundary."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .charging_cost_const import (
    CONF_AUTO_TARIFF_REFRESH,
    CONF_CHARGING_NETWORK_TARIFF,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_TARIFF_YEAR,
    NETWORK_MODULE_3,
)
from .entry_types import MennekesAmtronConfigEntry
from .network_tariff_config import parse_tariff_inputs
from .network_tariff_lookup import async_lookup_home_tariff

_LOGGER = logging.getLogger(__name__)
_RUNNING_STATES = (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
_TARIFF_ZONE = ZoneInfo("Europe/Berlin")


@callback
def async_track_tariff_midnight(
    hass: HomeAssistant, action: Callable[[datetime], None],
) -> CALLBACK_TYPE:
    """Retry expired tariffs at German midnight, independently of HA's zone."""
    unsubscribe: CALLBACK_TYPE

    def schedule() -> CALLBACK_TYPE:
        now = dt_util.utcnow().astimezone(_TARIFF_ZONE)
        next_run = datetime.combine(now.date(), time(0, 0, 5), _TARIFF_ZONE)
        if next_run <= now:
            next_run += timedelta(days=1)
        return async_track_point_in_utc_time(hass, fire, next_run.astimezone(UTC))

    @callback
    def fire(now: datetime) -> None:
        nonlocal unsubscribe
        # Recompute each local date so DST cannot shift the daily retry.
        unsubscribe = schedule()
        action(now)

    unsubscribe = schedule()

    @callback
    def cancel() -> None:
        unsubscribe()

    return cancel


def needs_year_refresh(options: dict[str, Any], *, year: int | None = None) -> bool:
    """Avoid external requests until an opted-in tariff has actually expired."""
    tariff = options.get(CONF_CHARGING_NETWORK_TARIFF)
    if options.get(CONF_AUTO_TARIFF_REFRESH) is not True or not isinstance(tariff, dict):
        return False
    if tariff.get("module") != NETWORK_MODULE_3:
        return False
    year = year or datetime.now(ZoneInfo("Europe/Berlin")).year
    configured_year = tariff.get(CONF_TARIFF_YEAR)
    return type(configured_year) is int and configured_year < year


async def async_refresh_tariff_year(
    hass: HomeAssistant, entry: MennekesAmtronConfigEntry,
) -> bool:
    """Apply a valid published tariff once, keeping all other options intact."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None or entry.state not in _RUNNING_STATES:
        return False
    options = dict(entry.options)
    if not needs_year_refresh(options):
        return False
    values = await async_lookup_home_tariff(hass)
    if (
        getattr(entry, "runtime_data", None) is not runtime
        or entry.state not in _RUNNING_STATES
    ):
        return False
    if values is None:
        _LOGGER.warning("No unambiguous published Module 3 tariff found for the new year; cost estimates remain unavailable until updated")
        return False
    try:
        previous = options[CONF_CHARGING_NETWORK_TARIFF]
        tariff = parse_tariff_inputs(
            {**values, CONF_PRICE_INCLUDES_NETWORK: previous[CONF_PRICE_INCLUDES_NETWORK]},
            module=NETWORK_MODULE_3,
            timezone="Europe/Berlin",
        )
    except (KeyError, TypeError, ValueError):
        _LOGGER.warning("Published Module 3 tariff could not be validated; keeping the existing estimate settings")
        return False
    if tariff[CONF_TARIFF_YEAR] != datetime.now(ZoneInfo("Europe/Berlin")).year:
        return False
    # An options edit or another refresh may have completed while HTTP was in
    # flight. Never replace the newer choices with this older snapshot.
    if dict(entry.options) != options:
        return False
    options[CONF_CHARGING_NETWORK_TARIFF] = tariff
    hass.config_entries.async_update_entry(entry, options=options)
    if values.get("_source_status") == "vorlaeufig":
        _LOGGER.warning("Applied provisional Module 3 prices for %s; review the operator's final price sheet when published", tariff[CONF_TARIFF_YEAR])
    else:
        _LOGGER.info("Updated Module 3 prices for %s from the published price sheet", tariff[CONF_TARIFF_YEAR])
    return True
