"""Actions of the MENNEKES AMTRON integration.

Writing 0 A to 0x0302 removes the charging limit instead of stopping the
charge. The number entity treats selecting zero as explicit intent; this
action additionally requires ``allow_unlimited`` for a zero-current request.
Pausing uses the dedicated charging-pause switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime
from typing import Any

import probatio
from homeassistant.auth.models import User
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .control import AmtronControl
from .data import MennekesAmtronRuntimeData
from .device.charge_job import MAX_BOOST_MINUTES
from .device.const import (
    ATTR_ALLOW_UNLIMITED,
    ATTR_CURRENT,
    DOMAIN,
    SERVICE_SET_CHARGING_CURRENT,
)
from .entity import entry_entity_unique_id
from .exceptions import service_validation_error
from .recorder_profile import async_setup_recorder_service
from .service_orders import ServiceOrders

SET_CHARGING_CURRENT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string]),
        probatio.Required(ATTR_CURRENT): probatio.All(
            probatio.Coerce(float), probatio.Range(min=0, max=32)
        ),
        probatio.Optional(ATTR_ALLOW_UNLIMITED, default=False): cv.boolean,
    }
)

# With one wallbox the target may be left out, which keeps dashboards short.
_DEVICE = {probatio.Optional(ATTR_DEVICE_ID): probatio.All(cv.ensure_list, [cv.string])}
_CURRENT = probatio.All(probatio.Coerce(float), probatio.Range(min=6, max=32))
_ENERGY = probatio.All(probatio.Coerce(float), probatio.Range(min=0.1, max=200))


def _aware(value: datetime) -> datetime:
    return dt_util.as_utc(value)


def _job(orders: ServiceOrders, data: dict[str, Any]) -> Awaitable[None]:
    start = _aware(data["start"]) if "start" in data else dt_util.utcnow()
    return orders.async_start_job(
        start=start, finish=_aware(data["finish"]), current=data["current"],
        energy=data["energy"], reserve=data.get("reserve", 0),
    )


# Action name, its fields beside the wallbox, and the call it makes.
ORDER_ACTIONS: dict[str, tuple[dict[Any, Any], Callable[[ServiceOrders, dict[str, Any]], Awaitable[None]]]] = {
    "start_charging_job": ({
        probatio.Optional("start"): cv.datetime,
        probatio.Required("finish"): cv.datetime,
        probatio.Required("current"): _CURRENT,
        probatio.Required("energy"): _ENERGY,
        probatio.Optional("reserve"): probatio.All(probatio.Coerce(float), probatio.Range(min=0, max=200)),
    }, _job),
    "start_guest_charging": ({
        probatio.Required("duration"): probatio.All(probatio.Coerce(int), probatio.Range(min=1, max=1440)),
        probatio.Required("current"): _CURRENT,
        probatio.Required("energy"): _ENERGY,
    }, lambda orders, data: orders.async_start_guest(
        minutes=data["duration"], current=data["current"], energy=data["energy"],
    )),
    "stop_charging_job": ({}, lambda orders, _data: orders.async_stop_job()),
    "clear_charging_job": ({}, lambda orders, _data: orders.async_clear_job()),
    "start_boost": ({
        probatio.Required("duration"): probatio.All(
            probatio.Coerce(int), probatio.Range(min=1, max=MAX_BOOST_MINUTES)
        ),
    }, lambda orders, data: orders.async_start_boost(minutes=data["duration"])),
    "cancel_boost": ({}, lambda orders, _data: orders.async_cancel_boost()),
    "activate_weekly_plan": ({
        probatio.Required("plan"): probatio.All(cv.string, probatio.Length(min=1, max=64)),
    }, lambda orders, data: orders.async_activate_weekly(plan=data["plan"])),
    "deactivate_weekly_plan": ({}, lambda orders, _data: orders.async_deactivate_weekly()),
}


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

    def order_handler(
        run: Callable[[ServiceOrders, dict[str, Any]], Awaitable[None]],
    ) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
        async def handle(call: ServiceCall) -> None:
            user = None
            if call.context.user_id:
                user = await hass.auth.async_get_user(call.context.user_id)
                if user is None:
                    raise UnknownUser(context=call.context)
            # Resolve and authorize every target before the first order is sent.
            targets = []
            for runtime in _resolve_runtimes(hass, call.data.get(ATTR_DEVICE_ID), user):
                if runtime.orders is None:
                    raise service_validation_error("app_required")
                targets.append(runtime.orders)
            for orders in targets:
                await run(orders, dict(call.data))

        return handle

    for name, (fields, run) in ORDER_ACTIONS.items():
        hass.services.async_register(
            DOMAIN, name, order_handler(run), schema=probatio.Schema({**_DEVICE, **fields}),
        )


@callback
def async_resolve_controls(
    hass: HomeAssistant, device_ids: list[str], user: User | None = None
) -> list[AmtronControl]:
    """Return the control objects of the targeted devices."""

    return [runtime.control for runtime in _resolve_runtimes(hass, device_ids, user)]


def _resolve_runtimes(
    hass: HomeAssistant, device_ids: list[str] | None, user: User | None = None
) -> list[MennekesAmtronRuntimeData]:
    """Return the targeted wallboxes; without a target, the only one there is."""
    targets: list[tuple[str, Any]] = []
    if device_ids is None:
        loaded = [
            entry for entry in hass.config_entries.async_entries(DOMAIN)
            if getattr(entry, "runtime_data", None) is not None
        ]
        if len(loaded) != 1:
            raise service_validation_error("wallbox_target_required")
        targets = [(loaded[0].entry_id, loaded[0])]
    else:
        registry = dr.async_get(hass)
        for device_id in device_ids:
            device = registry.async_get(device_id)
            if device is None:
                raise service_validation_error("unknown_device", {"device": device_id})
            entry = hass.config_entries.async_get_entry(device.config_entry_id)
            if entry is not None and entry.domain == DOMAIN:
                targets.append((device_id, entry))
    runtimes: list[MennekesAmtronRuntimeData] = []
    for device_id, entry in targets:
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
        runtimes.append(runtime_data)
    if not runtimes:
        raise service_validation_error("no_wallbox_target")
    return runtimes
