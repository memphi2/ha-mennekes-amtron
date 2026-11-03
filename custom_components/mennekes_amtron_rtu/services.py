"""Actions of the MENNEKES AMTRON integration.

The current action exists because its dangerous argument cannot
be offered safely as an entity: writing 0 A to 0x0302 removes the charging
limit instead of stopping the charge. The number entity therefore never
reaches 0 A, and a user who really wants "no limitation" has to say so
explicitly here.
"""

from __future__ import annotations

import probatio
from homeassistant.auth.models import User
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_ALLOW_UNLIMITED,
    ATTR_CURRENT,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from .control import AmtronControl
from .entity import entry_entity_unique_id
from .exceptions import service_validation_error
from .recorder_profile import async_setup_recorder_service

SET_CHARGING_CURRENT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Required(ATTR_CURRENT): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0, max=32)
        ),
        probatio.Optional(ATTR_ALLOW_UNLIMITED, default=False): cv.boolean,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once per Home Assistant start."""

    async_setup_recorder_service(hass)
    if hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT):
        return

    async def async_handle_set_charging_current(call: ServiceCall) -> None:
        user = None
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None:
                raise UnknownUser(context=call.context)
        current = float(call.data[ATTR_CURRENT])
        allow_unlimited = bool(call.data[ATTR_ALLOW_UNLIMITED])
        # Resolve and authorize every target before the first device is written.
        for control in async_resolve_controls(hass, call.data[ATTR_DEVICE_ID], user):
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
    hass: HomeAssistant, device_ids: list[str], user: User | None = None
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
            if user is not None and not user.is_admin:
                entity_id = er.async_get(hass).async_get_entity_id(
                    "number", DOMAIN, entry_entity_unique_id(entry, "charging_current_limit")
                )
                if entity_id is None or not user.permissions.check_entity(
                    entity_id, POLICY_CONTROL
                ):
                    raise Unauthorized(
                        user_id=user.id, entity_id=entity_id, permission=POLICY_CONTROL
                    )
            controls.append(runtime_data.control)
    if not controls:
        raise service_validation_error("no_wallbox_target")
    return controls
