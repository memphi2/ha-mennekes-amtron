"""Validate tariff input independently of Home Assistant configuration dialogs."""

from __future__ import annotations

import re
from typing import Any

from .charging_cost_const import (
    CONF_ACTIVE_QUARTERS,
    CONF_HIGH_NETWORK,
    CONF_HIGH_PERIODS,
    CONF_LOW_NETWORK,
    CONF_LOW_PERIODS,
    CONF_PRICE_INCLUDES_NETWORK,
    CONF_REGULAR_NETWORK,
    CONF_TARIFF_YEAR,
    NETWORK_MODULE_3,
)
from .device.charging_cost import TariffConfig


def parse_tariff_inputs(
    values: dict[str, Any], *, module: str, timezone: str
) -> dict[str, Any]:
    """Convert form controls into the model's strict tariff representation."""
    tariff = {
        "module": module,
        CONF_REGULAR_NETWORK: values[CONF_REGULAR_NETWORK],
        CONF_PRICE_INCLUDES_NETWORK: values[CONF_PRICE_INCLUDES_NETWORK],
    }
    if module == NETWORK_MODULE_3:
        quarters = values[CONF_ACTIVE_QUARTERS]
        if not isinstance(quarters, list) or any(
            value not in ("1", "2", "3", "4") for value in quarters
        ):
            raise ValueError("invalid active_quarters")
        tariff.update(
            {
                CONF_HIGH_NETWORK: values[CONF_HIGH_NETWORK],
                CONF_LOW_NETWORK: values[CONF_LOW_NETWORK],
                CONF_ACTIVE_QUARTERS: [int(value) for value in quarters],
                CONF_TARIFF_YEAR: values[CONF_TARIFF_YEAR],
                "timezone": timezone,
                "windows": [
                    *_tariff_periods(values[CONF_HIGH_PERIODS], "ht"),
                    *_tariff_periods(values[CONF_LOW_PERIODS], "nt"),
                ],
            }
        )
    parsed = TariffConfig.parse(tariff)
    if any(window.start % 15 or window.end % 15 for window in parsed.windows):
        raise ValueError("module 3 windows require quarter-hour boundaries")
    return parsed.as_dict()


def _tariff_periods(value: object, rate: str) -> list[dict[str, str]]:
    """Parse one HH:MM-HH:MM interval per line, including overnight windows."""
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ValueError("tariff periods must be short text")
    lines = value.splitlines()
    if not 1 <= len(lines) <= 8:
        raise ValueError("tariff periods require one to eight lines")
    windows: list[dict[str, str]] = []
    for line in lines:
        match = re.fullmatch(
            r"\s*([0-2][0-9]:[0-5][0-9])\s*[-\u2013]\s*([0-2][0-9]:[0-5][0-9])\s*", line
        )
        if match is None:
            raise ValueError("tariff period must be HH:MM-HH:MM")
        windows.append({"start": match[1], "end": match[2], "rate": rate})
    return windows


def tariff_form_values(tariff: dict[str, Any]) -> dict[str, Any]:
    values = dict(tariff)
    quarters = tariff.get(CONF_ACTIVE_QUARTERS)
    values[CONF_ACTIVE_QUARTERS] = (
        [str(value) for value in quarters] if isinstance(quarters, list) else []
    )
    windows = tariff.get("windows")
    windows = windows if isinstance(windows, list) else []
    for rate, key in (("ht", CONF_HIGH_PERIODS), ("nt", CONF_LOW_PERIODS)):
        values[key] = "\n".join(
            f"{window['start']}-{window['end']}"
            for window in windows
            if isinstance(window, dict)
            and window.get("rate") == rate
            and isinstance(window.get("start"), str)
            and isinstance(window.get("end"), str)
        )
    return values
