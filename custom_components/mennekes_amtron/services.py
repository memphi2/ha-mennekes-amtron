"""Actions of the MENNEKES AMTRON integration.

Only one action exists, and it exists because its dangerous argument cannot
be offered safely as an entity: writing 0 A to 0x0302 removes the charging
limit instead of stopping the charge. The number entity therefore never
reaches 0 A, and a user who really wants "no limitation" has to say so
explicitly here.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_ALLOW_UNLIMITED,
    ATTR_CURRENT,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from .control import AmtronControl
from .exceptions import service_validation_error

SET_CHARGING_CURRENT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_CURRENT): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=32)
        ),
        vol.Optional(ATTR_ALLOW_UNLIMITED, default=False): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once per Home Assistant start."""

    if hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT):
        return

    async def async_handle_set_charging_current(call: ServiceCall) -> None:
        current = float(call.data[ATTR_CURRENT])
        allow_unlimited = bool(call.data[ATTR_ALLOW_UNLIMITED])
        for control in async_resolve_controls(hass, call.data[ATTR_DEVICE_ID]):
            await control.async_set_charging_current(
                current, allow_unlimited=allow_unlimited
            )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_CHARGING_CURRENT,
        async_handle_set_charging_current,
        schema=SET_CHARGING_CURRENT_SCHEMA,
    )


@callback
def async_resolve_controls(
    hass: HomeAssistant, device_ids: list[str]
) -> list[AmtronControl]:
    """Return the control objects of the targeted devices."""

    registry = dr.async_get(hass)
    controls: list[AmtronControl] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            raise service_validation_error("unknown_device", {"device": device_id})
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            runtime_data = getattr(entry, "runtime_data", None)
            if runtime_data is None:
                raise service_validation_error(
                    "entry_not_loaded", {"device": device_id}
                )
            controls.append(runtime_data.control)
    if not controls:
        raise service_validation_error("no_wallbox_target")
    return controls
