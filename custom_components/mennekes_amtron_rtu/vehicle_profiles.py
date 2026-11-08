"""Bounded vehicle profiles, independent of manufacturer adapters and HA."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Final

CONF_VEHICLE_PROFILES: Final = "vehicle_profiles"
CONF_ACTIVE_VEHICLE_PROFILE: Final = "active_vehicle_profile"
CONF_VEHICLE_AUTO_SELECTION: Final = "vehicle_auto_selection"
CONF_VIRTUAL_PLUG_CHARGE: Final = "virtual_plug_charge"
CONF_VEHICLE_PLUG_CHARGE: Final = "vehicle_plug_charge"
CONF_VEHICLE_DETECTION_SOURCE: Final = "vehicle_detection_source"
CONF_VEHICLE_CONNECTED_ENTITY: Final = "vehicle_connected_entity"
CONF_VEHICLE_HOME_ENTITY: Final = "vehicle_home_entity"
CONF_VEHICLE_CONNECTED_STATES: Final = "vehicle_connected_states"
CONF_VEHICLE_PROFILE_ID: Final = "vehicle_profile_id"
CONF_VEHICLE_PROFILE_SELECTED_AT: Final = "vehicle_profile_selected_at"
CONF_VEHICLE_PROVIDER: Final = "vehicle_provider"
CONF_VEHICLE_DEVICE: Final = "vehicle_device"
CONF_VEHICLE_SOURCE_IDENTITY: Final = "vehicle_source_identity"
CONF_VEHICLE_SOC_USE_PROFILE: Final = "vehicle_soc_use_profile"
CONF_VEHICLE_LIMIT_USE_PROFILE: Final = "vehicle_limit_use_profile"
CONF_TIMED_USE_VEHICLE_CAPACITY: Final = "timed_use_vehicle_capacity"
LEGACY_PROFILE_ID: Final = "00000000000000000000000000000000"
MAX_VEHICLE_PROFILES: Final = 8
MAX_CONNECTED_STATES: Final = 8
RESET_PENDING: Final = "timed_goal_reset_pending"
PROFILE_OPTION_KEYS: Final = frozenset(
    {
        "vehicle_soc_entity",
        "vehicle_charge_limit_entity",
        CONF_VEHICLE_SOC_USE_PROFILE,
        CONF_VEHICLE_LIMIT_USE_PROFILE,
        CONF_VEHICLE_PLUG_CHARGE,
        "timed_goal_enabled",
        "timed_capacity_entity",
        "timed_capacity_kwh",
        CONF_TIMED_USE_VEHICLE_CAPACITY,
        "timed_calendar_entity",
        "timed_calendar_title",
        "timed_departure_entity",
        "timed_bavarian_device",
        "timed_vehicle_provider",
        "timed_vehicle_device",
        "timed_vehicle_time_zone",
        "timed_timer1_days",
        "timed_timer2_days",
        "charge_loss_source",
        "vehicle_last_charge_energy_entity",
        "charge_loss_auto_match",
        "charge_loss_bavarian_device",
        "charge_loss_vehicle_provider",
        "charge_loss_vehicle_device",
    }
)
_IDENTITY_KEYS = {
    CONF_VEHICLE_PROFILE_ID,
    CONF_VEHICLE_PROVIDER,
    CONF_VEHICLE_DEVICE,
    CONF_VEHICLE_SOURCE_IDENTITY,
}
_PROFILE_ID = re.compile(r"[0-9a-f]{32}")
_DETECTION_ENTITY = re.compile(r"(binary_sensor|sensor|device_tracker|person)\.[a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class VehicleDetectionConfig:
    """Explicit evidence sources; configuring them does not enable detection."""

    source: str
    connected_entity: str = ""
    home_entity: str = ""
    # A text sensor reports a plugged-in cable with one of these states.
    connected_states: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"source": self.source}
        if self.source == "entities":
            result.update(
                connected_entity=self.connected_entity, home_entity=self.home_entity
            )
            if self.connected_states:
                result["connected_states"] = list(self.connected_states)
        return result


def valid_detection_entity(value: Any, *, home: bool = False) -> bool:
    """Accept only bounded HA identifiers with the required semantics."""
    return (
        isinstance(value, str)
        and len(value) <= 255
        and _DETECTION_ENTITY.fullmatch(value) is not None
        and (
            value.startswith(("binary_sensor.", "device_tracker.", "person."))
            if home else value.startswith(("binary_sensor.", "sensor."))
        )
    )


def valid_connected_states(entity: str, value: Any) -> bool:
    """A text sensor needs its connected states; a binary sensor has none."""
    if not entity.startswith("sensor."):
        return value in (None, (), [])
    return (
        isinstance(value, (list, tuple))
        and 0 < len(value) <= MAX_CONNECTED_STATES
        and len(set(value)) == len(value)
        and all(
            isinstance(item, str) and item.strip() == item and 0 < len(item) <= 255
            and item not in ("unknown", "unavailable")
            for item in value
        )
    )


def _detection_config(value: Any) -> VehicleDetectionConfig | None:
    if not isinstance(value, dict):
        return None
    if value == {"source": "profile"}:
        return VehicleDetectionConfig("profile")
    if (
        not {"source", "connected_entity", "home_entity"} <= value.keys()
        <= {"source", "connected_entity", "home_entity", "connected_states"}
        or value["source"] != "entities"
        or not valid_detection_entity(value["connected_entity"])
        or not valid_detection_entity(value["home_entity"], home=True)
        or value["connected_entity"] == value["home_entity"]
        or not valid_connected_states(value["connected_entity"], value.get("connected_states"))
    ):
        return None
    return VehicleDetectionConfig(
        "entities", value["connected_entity"], value["home_entity"],
        tuple(value.get("connected_states", ())),
    )


@dataclass(frozen=True, slots=True)
class VehicleProfile:
    """One named local selection and its independently optional features."""

    id: str
    name: str
    provider_id: str
    device_id: str
    options: dict[str, Any]
    identity: str = ""
    detection: VehicleDetectionConfig | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "provider": self.provider_id,
            "device_id": self.device_id,
            "options": deepcopy(self.options),
        }
        if self.identity:
            result["identity"] = self.identity
        if self.detection is not None:
            result["detection"] = self.detection.as_dict()
        return result


def profiles(raw: Mapping[str, Any]) -> dict[str, VehicleProfile]:
    """Read a bounded valid catalog; malformed catalogs cannot revive old data."""
    catalog = raw.get(CONF_VEHICLE_PROFILES)
    if not isinstance(catalog, dict) or len(catalog) > MAX_VEHICLE_PROFILES:
        return {}
    result: dict[str, VehicleProfile] = {}
    for key, value in catalog.items():
        if (
            not isinstance(key, str)
            or _PROFILE_ID.fullmatch(key) is None
            or not isinstance(value, dict)
            or not {"name", "provider", "device_id", "options"} <= value.keys()
            or not value.keys()
            <= {"name", "provider", "device_id", "options", "identity", "detection"}
        ):
            return {}
        name, provider, device, options = (
            value["name"],
            value["provider"],
            value["device_id"],
            value["options"],
        )
        identity = value.get("identity", "")
        detection = _detection_config(value.get("detection"))
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name) > 80
            or not isinstance(provider, str)
            or not provider
            or len(provider) > 64
            or not isinstance(device, str)
            or len(device) > 128
            or (provider != "none" and not device)
            or not isinstance(options, dict)
            or not options.keys() <= PROFILE_OPTION_KEYS
            or (
                CONF_VEHICLE_PLUG_CHARGE in options
                and type(options[CONF_VEHICLE_PLUG_CHARGE]) is not bool
            )
            or not isinstance(identity, str)
            or len(identity) > 256
            or ("detection" in value and detection is None)
            or (
                detection is not None
                and detection.source == "profile"
                and provider == "none"
            )
            or any(
                item.name.casefold() == name.strip().casefold()
                for item in result.values()
            )
        ):
            return {}
        result[key] = VehicleProfile(
            key, name, provider, device, deepcopy(options), identity, detection
        )
    return result


def profile_list(raw: Mapping[str, Any]) -> tuple[VehicleProfile, ...]:
    """Preserve the user's profile order for selectors."""
    return tuple(profiles(raw).values())


def active_profile(raw: Mapping[str, Any]) -> VehicleProfile | None:
    """Resolve only an explicit active selection, never the first available car."""
    key = raw.get(CONF_ACTIVE_VEHICLE_PROFILE)
    return profiles(raw).get(key) if isinstance(key, str) else None


def vehicle_options(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Project the active profile into the existing feature settings contract."""
    if CONF_VEHICLE_PROFILES not in raw:
        return deepcopy(dict(raw))
    result = {
        key: deepcopy(value)
        for key, value in raw.items()
        if key not in PROFILE_OPTION_KEYS | _IDENTITY_KEYS
        and key not in {CONF_VEHICLE_PROFILES, CONF_ACTIVE_VEHICLE_PROFILE}
    }
    if (profile := active_profile(raw)) is None:
        result["timed_goal_enabled"] = False
        return result
    result.update(deepcopy(profile.options))
    result.update(
        {
            CONF_VEHICLE_PROFILE_ID: profile.id,
            CONF_VEHICLE_PROVIDER: profile.provider_id,
            CONF_VEHICLE_DEVICE: profile.device_id,
            CONF_VEHICLE_SOURCE_IDENTITY: profile.identity,
        }
    )
    if profile.provider_id == "none":
        for key in (
            CONF_VEHICLE_SOC_USE_PROFILE,
            CONF_VEHICLE_LIMIT_USE_PROFILE,
            CONF_TIMED_USE_VEHICLE_CAPACITY,
        ):
            result.pop(key, None)
    return result


def vehicle_soc_is_configured(effective: Mapping[str, Any]) -> bool:
    """A manually selected SoC or explicitly enabled profile telemetry is usable."""
    if effective.get(CONF_VEHICLE_SOC_USE_PROFILE) is True:
        return effective.get(CONF_VEHICLE_PROVIDER) not in (None, "none", "") and bool(
            effective.get(CONF_VEHICLE_DEVICE)
        )
    return bool(effective.get("vehicle_soc_entity"))


def activate_profile(raw: Mapping[str, Any], profile_id: str | None) -> dict[str, Any]:
    """Switch atomically and invalidate the former vehicle's next-trip plan."""
    if profile_id is not None and profile_id not in profiles(raw):
        raise ValueError("unknown vehicle profile")
    result = deepcopy(dict(raw))
    if result.get(CONF_ACTIVE_VEHICLE_PROFILE) != profile_id:
        result[CONF_ACTIVE_VEHICLE_PROFILE] = profile_id
        result[RESET_PENDING] = True
        result[CONF_VEHICLE_PROFILE_SELECTED_AT] = time.time()
    return result


# Changing only these keys switches the vehicle at runtime instead of reloading.
SELECTION_KEYS: Final = frozenset(
    {
        CONF_ACTIVE_VEHICLE_PROFILE,
        CONF_VEHICLE_PROFILE_SELECTED_AT,
        CONF_VEHICLE_AUTO_SELECTION,
        RESET_PENDING,
    }
)


def without_selection(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a vehicle switch must leave unchanged."""
    return {key: value for key, value in raw.items() if key not in SELECTION_KEYS}


def each_vehicle_options(raw: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Project every profile, so entities exist for any car that uses them."""
    catalog = profiles(raw)
    if not catalog:
        return (vehicle_options(raw),)
    return tuple(
        vehicle_options({**raw, CONF_ACTIVE_VEHICLE_PROFILE: key}) for key in catalog
    )
