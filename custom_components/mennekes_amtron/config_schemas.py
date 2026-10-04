"""Voluptuous schemas for the config and options flows."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from ._flow_serial import PortOption
from .const import (
    CONF_BAUDRATE,
    CONF_CONTROL_MODE,
    CONF_CURRENT_LIMIT,
    CONF_DEVICE_ID,
    CONF_FRAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL_SECONDS,
    CONF_SEARCH,
    CONTROL_MODES,
    DEFAULT_BAUDRATE,
    DEFAULT_CONTROL_MODE,
    DEFAULT_DEVICE_ID,
    DEFAULT_FRAME,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DEFAULT_SEARCH,
    DEVICE_ID_MAX,
    DEVICE_ID_MIN,
    MAX_SCAN_INTERVAL_SECONDS,
    MIN_SCAN_INTERVAL_SECONDS,
    SEARCH_MODES,
    SUPPORTED_BAUDRATES,
    SUPPORTED_FRAMES,
)


def user_schema(ports: list[PortOption]) -> vol.Schema:
    """Return the schema of the first config-flow step."""

    return vol.Schema(
        {
            vol.Required(CONF_PORT): _port_selector(ports),
            # A bare integer with a range renders as a slider, which is the
            # wrong control for a bus address somebody reads off a tool.
            vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): vol.All(
                NumberSelector(
                    NumberSelectorConfig(
                        min=DEVICE_ID_MIN,
                        max=DEVICE_ID_MAX,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Coerce(int),
            ),
            vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
                SUPPORTED_BAUDRATES
            ),
            vol.Required(CONF_FRAME, default=DEFAULT_FRAME): SelectSelector(
                SelectSelectorConfig(
                    options=list(SUPPORTED_FRAMES),
                    translation_key="frame",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_SEARCH, default=DEFAULT_SEARCH): SelectSelector(
                SelectSelectorConfig(
                    options=list(SEARCH_MODES),
                    translation_key="search",
                    mode=SelectSelectorMode.LIST,
                )
            ),
        }
    )


def options_schema(
    current: dict[str, Any],
    *,
    max_current: float,
) -> vol.Schema:
    """Return the options schema, bounded by what the device allows."""

    return vol.Schema(
        {
            vol.Required(
                CONF_CONTROL_MODE,
                default=current.get(CONF_CONTROL_MODE, DEFAULT_CONTROL_MODE),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=list(CONTROL_MODES),
                    translation_key="control_mode",
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_SCAN_INTERVAL_SECONDS,
                default=current.get(
                    CONF_SCAN_INTERVAL_SECONDS, DEFAULT_SCAN_INTERVAL_SECONDS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_SECONDS,
                    max=MAX_SCAN_INTERVAL_SECONDS,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_CURRENT_LIMIT,
                default=current.get(CONF_CURRENT_LIMIT, max_current),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=6,
                    max=max(max_current, 6),
                    step=0.1,
                    unit_of_measurement="A",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _port_selector(ports: list[PortOption]) -> Any:
    if not ports:
        return cv.string
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=port.value, label=port.label)
                for port in ports
            ],
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
