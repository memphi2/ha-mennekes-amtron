"""Explicit vehicle profile management, separate from optional charging features."""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import SelectOptionDict

from ._options_detection import DetectionOptionsFlow
from .config_schemas import (
    vehicle_device_schema,
    vehicle_profile_choice_schema,
    vehicle_profile_details_schema,
)
from .vehicle_profiles import (
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_DEVICE,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_PROVIDER,
    LEGACY_PROFILE_ID,
    MAX_VEHICLE_PROFILES,
    PROFILE_OPTION_KEYS,
    RESET_PENDING,
    VehicleProfile,
    activate_profile,
    active_profile,
    profiles,
)
from .vehicle_sources import provider_device_is_valid, provider_identity

_PROFILE_KEYS = {
    CONF_VEHICLE_PROFILES,
    CONF_ACTIVE_VEHICLE_PROFILE,
    CONF_VEHICLE_AUTO_SELECTION,
    CONF_VEHICLE_PROFILE_SELECTED_AT,
    RESET_PENDING,
} | set(PROFILE_OPTION_KEYS)


class ProfileOptionsFlow(DetectionOptionsFlow):
    """Keep a small explicit catalog and never select another car implicitly."""

    def _profile_providers(self) -> list[str]:
        """Offer only installed vehicle sources with a real local vehicle."""
        providers = ["none"]
        # Iterating the device registry yields device entries, not ids. Ask
        # for the devices of each vehicle integration entry instead.
        registry = dr.async_get(self.hass)
        if any(
            provider_device_is_valid(self.hass, "bavariandata", device.id)
            for entry in self.hass.config_entries.async_entries("bavariandata")
            for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        ):
            providers.append("bavariandata")
        return providers

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List the cars; each car's own settings are reached through its profile."""
        draft = self._begin("profiles", _PROFILE_KEYS)
        self._profile_target = None
        choices = ["vehicle_profile_add"]
        if CONF_VEHICLE_PROFILES not in self._options:
            choices.append("vehicle_settings")
        elif profiles(draft.values):
            choices += [
                "vehicle_profile_open",
                "vehicle_profile_select",
                "vehicle_profile_remove",
                "vehicle_profile_clear",
            ]
        selected = active_profile(draft.values)
        return self.async_show_menu(
            step_id="vehicle",
            menu_options=[*choices, "back"],
            description_placeholders={
                "vehicle": selected.name if selected is not None else "—",
            },
        )

    async def async_step_vehicle_profile_open(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        catalog = profiles(self._options)
        if not catalog:
            return await self.async_step_vehicle()
        key = next(iter(catalog)) if len(catalog) == 1 else None
        errors = {}
        if user_input is not None:
            key = user_input.get("vehicle_profile_id")
            if not isinstance(key, str) or key not in catalog:
                key = None
                errors["vehicle_profile_id"] = "vehicle_profile_required"
        if key is not None:
            # Opening a car guides through all of its settings.
            self._profile_target = key
            self._start_wizard()
            return await self.async_step_vehicle_profile_edit()
        choices: list[SelectOptionDict] = [
            {"value": item.id, "label": item.name} for item in catalog.values()
        ]
        return self.async_show_form(
            step_id="vehicle_profile_open",
            errors=errors,
            data_schema=vehicle_profile_choice_schema(
                choices, self._options.get(CONF_ACTIVE_VEHICLE_PROFILE)
            ),
        )

    async def async_step_vehicle_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Without profiles, the vehicle settings are shared by every car."""
        self._profile_target = None
        return await self.async_step_vehicle_profile()

    async def async_step_vehicle_profile(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Everything that belongs to one car, in one place."""
        self._draft = None
        self._details = {}
        if not self._target_ready():
            return await self.async_step_vehicle()
        profile = self._target_profile()
        choices = ["vehicle_battery", "departure", "losses"]
        if profile is not None:
            choices = [
                "vehicle_profile_edit",
                *choices,
                "vehicle_detection_signals",
            ]
        return self.async_show_menu(
            step_id="vehicle_profile" if profile is not None else "vehicle_settings",
            menu_options=[*choices, "vehicle"],
            description_placeholders=self._vehicle_placeholders(),
        )

    async def async_step_vehicle_profile_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # A new car is guided through all of its settings.
        if self._wizard is None:
            self._start_wizard()
        return await self._profile_details(False, user_input)

    async def async_step_vehicle_profile_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            if (profile := self._target_profile()) is None:
                return await self.async_step_vehicle()
            self._begin("profiles", _PROFILE_KEYS)
            self._details["profile_edit"] = profile.id
        return await self._profile_details(True, user_input)

    async def async_step_vehicle_profile_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._profile_choice("select", user_input)

    async def async_step_vehicle_profile_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._profile_choice("remove", user_input)

    async def async_step_vehicle_profile_clear(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("profiles")) is None:
            return await self.async_step_init()
        draft.values = activate_profile(draft.values, None)
        draft.values[CONF_VEHICLE_AUTO_SELECTION] = False
        return await self._finish_section()

    async def _profile_choice(
        self, action: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if (draft := self._section("profiles")) is None:
            return await self.async_step_init()
        catalog = profiles(draft.values)
        errors = {}
        if user_input is not None:
            key = user_input.get("vehicle_profile_id")
            if not isinstance(key, str) or key not in catalog:
                errors["vehicle_profile_id"] = "vehicle_profile_required"
            elif action == "remove" and user_input.get("confirm") is not True:
                errors["confirm"] = "vehicle_profile_confirm_required"
            else:
                if action == "select":
                    draft.values = activate_profile(draft.values, key)
                    draft.values[CONF_VEHICLE_AUTO_SELECTION] = False
                else:
                    draft.values[CONF_VEHICLE_PROFILES].pop(key)
                    if draft.values.get(CONF_ACTIVE_VEHICLE_PROFILE) == key:
                        draft.values = activate_profile(draft.values, None)
                return await self._finish_section()
        choices: list[SelectOptionDict] = [
            {"value": item.id, "label": item.name} for item in catalog.values()
        ]
        return self.async_show_form(
            step_id=f"vehicle_profile_{action}",
            errors=errors,
            data_schema=vehicle_profile_choice_schema(
                choices,
                draft.values.get(CONF_ACTIVE_VEHICLE_PROFILE),
                remove=action == "remove",
            ),
            last_step=True,
        )

    async def _profile_details(
        self, edit: bool, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if (draft := self._section("profiles")) is None:
            return await self.async_step_init()
        catalog = profiles(draft.values)
        key = self._details.get("profile_edit") if edit else None
        existing = catalog.get(key) if isinstance(key, str) else None
        if edit and existing is None:
            return await self.async_step_vehicle()
        available_providers = self._profile_providers()
        selectable_providers = available_providers.copy()
        if existing is not None and existing.provider_id not in selectable_providers:
            selectable_providers.append(existing.provider_id)
        errors = {}
        current: dict[str, Any] = {
            "vehicle_profile_name": existing.name if existing else "",
            CONF_VEHICLE_PROVIDER: existing.provider_id if existing else "none",
        }
        if user_input is not None:
            current = user_input
            name = user_input.get("vehicle_profile_name")
            provider = user_input.get(CONF_VEHICLE_PROVIDER)
            legacy_count = int(
                CONF_VEHICLE_PROFILES not in draft.values
                and any(option in draft.values for option in PROFILE_OPTION_KEYS)
            )
            if not edit and len(catalog) + legacy_count >= MAX_VEHICLE_PROFILES:
                errors["base"] = "vehicle_profile_limit"
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name.strip()) > 80
                or any(
                    item.id != key and item.name.casefold() == name.strip().casefold()
                    for item in catalog.values()
                )
            ):
                errors["vehicle_profile_name"] = "vehicle_profile_name_invalid"
            if provider not in selectable_providers:
                errors[CONF_VEHICLE_PROVIDER] = "vehicle_provider_invalid"
            if not errors:
                self._details.update(
                    profile_name=str(name).strip(),
                    profile_provider=provider,
                    profile_key=key,
                )
                if provider == "none":
                    return await self._save_profile("")
                if existing is not None and provider not in available_providers:
                    return await self._save_profile(existing.device_id, existing.identity)
                return await self.async_step_vehicle_profile_device()
        return self.async_show_form(
            step_id=f"vehicle_profile_{'edit' if edit else 'add'}",
            data_schema=vehicle_profile_details_schema(current, providers=selectable_providers),
            errors=errors,
        )

    async def async_step_vehicle_profile_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if (draft := self._section("profiles")) is None or self._details.get(
            "profile_provider"
        ) != "bavariandata":
            return await self.async_step_init()
        errors = {}
        if user_input is not None:
            device = user_input.get(CONF_VEHICLE_DEVICE)
            if not isinstance(device, str) or not provider_device_is_valid(
                self.hass, "bavariandata", device
            ):
                errors[CONF_VEHICLE_DEVICE] = "vehicle_device_invalid"
            else:
                identity = provider_identity(self.hass, "bavariandata", device)
                if identity is None:
                    errors[CONF_VEHICLE_DEVICE] = "vehicle_device_invalid"
                else:
                    return await self._save_profile(device, identity)
        key = self._details.get("profile_key")
        existing = profiles(draft.values).get(key) if isinstance(key, str) else None
        return self.async_show_form(
            step_id="vehicle_profile_device",
            errors=errors,
            last_step=True,
            data_schema=vehicle_device_schema(
                user_input
                if user_input is not None
                else {
                    CONF_VEHICLE_DEVICE: existing.device_id if existing else None,
                },
                key=CONF_VEHICLE_DEVICE,
                integration="bavariandata",
            ),
        )

    async def _save_profile(self, device: str, identity: str = "") -> ConfigFlowResult:
        draft = self._section("profiles")
        if draft is None:
            raise RuntimeError("cannot save an incomplete profile")
        if CONF_VEHICLE_PROFILES not in draft.values:
            legacy = {
                key: deepcopy(draft.values[key])
                for key in PROFILE_OPTION_KEYS
                if key in draft.values
            }
            draft.clear(set(PROFILE_OPTION_KEYS))
            draft.values[CONF_VEHICLE_PROFILES] = {}
            if legacy:
                draft.values[CONF_VEHICLE_PROFILES][LEGACY_PROFILE_ID] = VehicleProfile(
                    LEGACY_PROFILE_ID,
                    "Existing sources (1)"
                    if self._details["profile_name"].casefold() == "existing sources"
                    else "Existing sources",
                    "none",
                    "",
                    legacy,
                ).as_dict()
                draft.values[CONF_ACTIVE_VEHICLE_PROFILE] = LEGACY_PROFILE_ID
        key = self._details["profile_key"] or uuid4().hex
        # The guided setup continues with this car's settings.
        self._profile_target = key
        existing = profiles(draft.values).get(key)
        provider = self._details["profile_provider"]
        same_identity = existing is not None and (
            existing.provider_id,
            existing.device_id,
            existing.identity,
        ) == (provider, device, identity)
        options = existing.options if existing is not None and same_identity else {}
        draft.values[CONF_VEHICLE_PROFILES][key] = VehicleProfile(
            key,
            self._details["profile_name"],
            provider,
            device,
            options,
            identity,
            existing.detection if existing is not None and same_identity else None,
        ).as_dict()
        if CONF_ACTIVE_VEHICLE_PROFILE not in draft.values:
            draft.values = activate_profile(draft.values, key)
        elif not same_identity and draft.values.get(CONF_ACTIVE_VEHICLE_PROFILE) == key:
            draft.values[RESET_PENDING] = True
            draft.values[CONF_VEHICLE_PROFILE_SELECTED_AT] = time.time()
        return await self._finish_section()
