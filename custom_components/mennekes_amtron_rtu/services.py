"""Actions of the MENNEKES AMTRON integration.

Writing 0 A to 0x0302 removes the charging limit instead of stopping the
charge. The number entity treats selecting zero as explicit intent; this
action additionally requires ``allow_unlimited`` for a zero-current request.
Pausing uses the dedicated charging-pause switch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.auth.models import User
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_platform_entity_service

from .const import ATTR_ALLOW_UNLIMITED, ATTR_CURRENT, SERVICE_SET_CHARGING_CURRENT
from .data import MennekesAmtronRuntimeData
from .device.const import DOMAIN
from .entity import entry_entity_unique_id
from .exceptions import service_validation_error
from .recorder_profile import async_setup_recorder_service
from .service_control import ServiceControl

if TYPE_CHECKING:
    from .charge_loss_observer import ChargeLossObserver
    from .departure_observer import DepartureObserver

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


def _departure_planning(runtime: MennekesAmtronRuntimeData) -> DepartureObserver:
    if runtime.departure_observer is None:
        raise service_validation_error("departure_unavailable")
    return runtime.departure_observer


def _loss_estimate(runtime: MennekesAmtronRuntimeData) -> ChargeLossObserver:
    if runtime.charge_loss is None:
        raise service_validation_error("loss_unavailable")
    return runtime.charge_loss


async def _use_calendar_departure(runtime: MennekesAmtronRuntimeData) -> None:
    await _departure_planning(runtime).async_use_next_calendar_event()


async def _use_vehicle_departure(runtime: MennekesAmtronRuntimeData) -> None:
    await _departure_planning(runtime).async_use_vehicle_departure()


async def _assign_vehicle_charge(runtime: MennekesAmtronRuntimeData) -> None:
    await _loss_estimate(runtime).async_assign()


# Actions on a wallbox that have no state of their own. Each takes what it
# needs from the wallbox's ordinary controls and settings.
DEVICE_ACTIONS: dict[str, Callable[[MennekesAmtronRuntimeData], Awaitable[None]]] = {
    "start_guest_charging": lambda runtime: runtime.guest.async_start_guest(),
    "use_calendar_departure": _use_calendar_departure,
    "use_vehicle_departure": _use_vehicle_departure,
    "assign_vehicle_charge": _assign_vehicle_charge,
}
# What a wallbox needs for an action. Every target is checked before the
# first one acts, so a call never does half its work.
_ACTION_NEEDS: dict[str, Callable[[MennekesAmtronRuntimeData], object]] = {
    "use_calendar_departure": _departure_planning,
    "use_vehicle_departure": _departure_planning,
    "assign_vehicle_charge": _loss_estimate,
}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions once per Home Assistant start."""

    async_setup_recorder_service(hass)
    if hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_CURRENT):
        return

    async_register_platform_entity_service(
        hass, DOMAIN, "clear_target_time", entity_domain="datetime",
        schema={}, func="async_clear_value",
    )

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

    def device_handler(
        name: str, run: Callable[[MennekesAmtronRuntimeData], Awaitable[None]],
    ) -> Callable[[ServiceCall], Coroutine[Any, Any, None]]:
        async def handle(call: ServiceCall) -> None:
            user = None
            if call.context.user_id:
                user = await hass.auth.async_get_user(call.context.user_id)
                if user is None:
                    raise UnknownUser(context=call.context)
            # Resolve, authorize and check every target before the first one acts.
            runtimes = _resolve_runtimes(hass, call.data.get(ATTR_DEVICE_ID), user)
            if (needs := _ACTION_NEEDS.get(name)) is not None:
                for runtime in runtimes:
                    needs(runtime)
            for runtime in runtimes:
                await run(runtime)

        return handle

    for name, run in DEVICE_ACTIONS.items():
        hass.services.async_register(
            DOMAIN, name, device_handler(name, run), schema=probatio.Schema(_DEVICE),
        )


@callback
def async_resolve_controls(
    hass: HomeAssistant, device_ids: list[str], user: User | None = None
) -> list[ServiceControl]:
    """Return the control objects of the targeted devices."""

    return [runtime.control for runtime in _resolve_runtimes(hass, device_ids, user)]


def _loaded_runtime(entry: Any) -> MennekesAmtronRuntimeData | None:
    """The entry's runtime while it is loaded; a failed setup leaves a stopped one behind."""
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return getattr(entry, "runtime_data", None)


def _resolve_runtimes(
    hass: HomeAssistant, device_ids: list[str] | None, user: User | None = None
) -> list[MennekesAmtronRuntimeData]:
    """Return the targeted wallboxes; without a target, the only one there is."""
    targets: list[tuple[str, Any]] = []
    if device_ids is None:
        loaded = [
            entry for entry in hass.config_entries.async_entries(DOMAIN)
            if _loaded_runtime(entry) is not None
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
        runtime_data = _loaded_runtime(entry)
        if runtime_data is None:
            raise service_validation_error(
                "entry_not_loaded", {"device": device_id}
            )
        if user is not None and not user.is_admin:
            entities = er.async_get(hass)
            # A read-only wallbox has no controls; its status stands for it.
            entity_id = entities.async_get_entity_id(
                "number", DOMAIN, entry_entity_unique_id(entry, "charging_current_limit")
            ) or entities.async_get_entity_id(
                "sensor", DOMAIN, entry_entity_unique_id(entry, "charging_status")
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
