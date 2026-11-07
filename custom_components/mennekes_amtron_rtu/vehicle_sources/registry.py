"""Resolve optional local vehicle adapters while retaining saved selections."""

from collections.abc import Collection, Mapping
from typing import Any

from homeassistant.core import HomeAssistant

from custom_components.mennekes_amtron_rtu.charge_loss_const import (
    CONF_CHARGE_LOSS_SOURCE,
    CONF_LOSS_BAVARIAN_DEVICE,
    LOSS_SOURCE_BAVARIANDATA,
    LOSS_SOURCE_DISABLED,
    LOSS_SOURCE_VEHICLE_HISTORY,
)
from custom_components.mennekes_amtron_rtu.timed_goal_const import (
    CONF_TIMED_BAVARIAN_DEVICE,
    CONF_TIMED_TIMER1_DAYS,
    CONF_TIMED_TIMER2_DAYS,
    CONF_TIMED_VEHICLE_TIME_ZONE,
)

from .bavariandata import BavarianChargeHistorySource, BavarianDepartureSource
from .bavariandata.history import bavarian_vehicle_vin
from .const import (
    CONF_LOSS_VEHICLE_DEVICE,
    CONF_LOSS_VEHICLE_PROVIDER,
    CONF_TIMED_VEHICLE_DEVICE,
    CONF_TIMED_VEHICLE_PROVIDER,
    PROVIDER_BAVARIANDATA,
    PROVIDER_NONE,
)
from .types import (
    Capability,
    ChargeHistorySource,
    DepartureSource,
    ProviderDefinition,
    ProviderSelection,
)

_PROVIDERS = (
    ProviderDefinition(PROVIDER_BAVARIANDATA, "bavariandata", frozenset({"departure", "charge_history"})),
)


def provider_definitions(capability: Capability) -> tuple[ProviderDefinition, ...]:
    """Expose implemented capability metadata for optional source selection."""
    return tuple(provider for provider in _PROVIDERS if capability in provider.capabilities)


def provider_selection(options: Mapping[str, Any], capability: Capability) -> ProviderSelection:
    """An explicit generic selection always wins over retained legacy options."""
    provider_key, device_key, legacy_key = (
        (CONF_TIMED_VEHICLE_PROVIDER, CONF_TIMED_VEHICLE_DEVICE, CONF_TIMED_BAVARIAN_DEVICE)
        if capability == "departure" else
        (CONF_LOSS_VEHICLE_PROVIDER, CONF_LOSS_VEHICLE_DEVICE, CONF_LOSS_BAVARIAN_DEVICE)
    )
    if provider_key not in options and device_key not in options:
        device = options.get(legacy_key)
        if isinstance(device, str) and device:
            return ProviderSelection(PROVIDER_BAVARIANDATA, device)
        return ProviderSelection(PROVIDER_NONE, "")
    provider = options.get(provider_key)
    device = options.get(device_key)
    return ProviderSelection(
        provider if isinstance(provider, str) and provider else PROVIDER_NONE,
        device if isinstance(device, str) else "",
    )


def provider_device_is_valid(hass: HomeAssistant, provider_id: str, device_id: object) -> bool:
    """Only a concrete supported vehicle device can provide these capabilities."""
    return provider_id == PROVIDER_BAVARIANDATA and bavarian_vehicle_vin(hass, device_id) is not None


def _weekdays(value: Any) -> Collection[str]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(day, str) for day in value):
        return ()
    return value


def resolve_departure_source(hass: HomeAssistant, options: Mapping[str, Any]) -> DepartureSource | None:
    """Generic HA timestamp/calendar/manual inputs do not require an adapter."""
    selection = provider_selection(options, "departure")
    if selection.provider_id != PROVIDER_BAVARIANDATA or not selection.device_id:
        return None
    zone = options.get(CONF_TIMED_VEHICLE_TIME_ZONE, hass.config.time_zone)
    return BavarianDepartureSource(
        hass, selection.device_id, time_zone=zone if isinstance(zone, str) else "",
        timer1_days=_weekdays(options.get(CONF_TIMED_TIMER1_DAYS, ())),
        timer2_days=_weekdays(options.get(CONF_TIMED_TIMER2_DAYS, ())),
    )


def resolve_history_source(hass: HomeAssistant, options: Mapping[str, Any]) -> ChargeHistorySource | None:
    """Bind history independently of the device selected for departure timers."""
    selection = provider_selection(options, "charge_history")
    if selection.provider_id != PROVIDER_BAVARIANDATA or not selection.device_id:
        return None
    return BavarianChargeHistorySource(hass, selection.device_id)


def loss_source(options: Mapping[str, Any]) -> str:
    """Canonicalize the old manufacturer-specific mode at the configuration edge."""
    source = options.get(CONF_CHARGE_LOSS_SOURCE, LOSS_SOURCE_DISABLED)
    if source == LOSS_SOURCE_BAVARIANDATA:
        return LOSS_SOURCE_VEHICLE_HISTORY
    return source if isinstance(source, str) else LOSS_SOURCE_DISABLED
