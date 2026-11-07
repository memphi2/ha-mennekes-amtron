"""Suggest Recorder exclusions using registered IDs; never change Recorder."""

from __future__ import annotations

import probatio
import yaml
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .device.const import DOMAIN
from .entity import entry_entity_unique_id
from .exceptions import service_validation_error

SERVICE = "get_recorder_exclusions"
DETAIL_KEYS = (
    *tuple(
        f"{kind}_l{phase}"
        for kind in ("current", "voltage", "power")
        for phase in (1, 2, 3)
    ),
    "session_duration",
)
EXTRA_KEYS = (
    "signaled_current",
    "downgrade_current",
    "session_energy",
    "sessions_total",
    "temperature",
)
PROFILES = {"full": (), "detail": DETAIL_KEYS, "lean": DETAIL_KEYS + EXTRA_KEYS}
SCHEMA = probatio.Schema(
    {
        probatio.Required("device_id"): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Optional("profile", default="detail"): probatio.In(PROFILES),
    }
)


@callback
def async_setup_recorder_service(hass: HomeAssistant) -> None:
    """Register a read-only action, also available in read-only control mode."""
    if hass.services.has_service(DOMAIN, SERVICE):
        return

    async def handle(call: ServiceCall) -> ServiceResponse:
        user = None
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None:
                raise UnknownUser(context=call.context)
        profile = str(call.data["profile"])
        registry = er.async_get(hass)
        devices = dr.async_get(hass)
        entities: set[str] = set()
        matched = False
        for device_id in call.data["device_id"]:
            device = devices.async_get(device_id)
            if device is None:
                raise service_validation_error("unknown_device", {"device": device_id})
            entry = hass.config_entries.async_get_entry(device.config_entry_id)
            if entry is None or entry.domain != DOMAIN:
                continue
            matched = True
            for key in PROFILES[profile]:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, entry_entity_unique_id(entry, key)
                )
                if entity_id is None:
                    continue
                registered = registry.async_get(entity_id)
                if registered is None or registered.device_id != device_id:
                    continue
                if user is not None and not user.permissions.check_entity(
                    entity_id, POLICY_READ
                ):
                    raise Unauthorized(
                        user_id=user.id, entity_id=entity_id, permission=POLICY_READ
                    )
                entities.add(entity_id)
        if not matched:
            raise service_validation_error("no_wallbox_target")
        selected = sorted(entities)
        return {
            "profile": profile,
            "entities": selected,
            "yaml": yaml.safe_dump(
                {"recorder": {"exclude": {"entities": selected}}}, sort_keys=False
            ),
        }

    hass.services.async_register(
        DOMAIN, SERVICE, handle, schema=SCHEMA, supports_response=SupportsResponse.ONLY
    )
