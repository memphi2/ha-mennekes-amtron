"""Common draft and concurrent-edit protection for options sections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from ._options_draft import OptionsChangedError, OptionsDraft
from .config_schemas import vehicle_provider_schema
from .vehicle_profiles import (
    CONF_VEHICLE_DEVICE,
    CONF_VEHICLE_PROFILES,
    CONF_VEHICLE_PROVIDER,
    VehicleProfile,
    profile_options,
    profiles,
)
from .vehicle_sources import (
    Capability,
    ProviderSelection,
    profile_capabilities,
    provider_definitions,
    provider_device_is_valid,
    provider_identity,
    provider_selection,
)
from .vehicle_sources.const import PROVIDER_NONE

# A profile's settings, in the order the guided profile setup visits them.
WIZARD_AREAS = ("vehicle_battery", "departure", "losses", "vehicle_detection_signals")
_MISSING = object()


class SectionOptionsFlow(OptionsFlow):
    """Save only a complete section, preserving changes in other sections."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: OptionsDraft | None = None
        self._details: dict[str, Any] = {}
        # The profile whose settings are being edited; None without profiles.
        self._profile_target: str | None = None
        # The guided profile setup: the areas still to come, and the options
        # it collects. Nothing is saved before its end.
        self._wizard: list[str] | None = None
        self._wizard_start: dict[str, Any] | None = None
        self._wizard_options: dict[str, Any] | None = None

    @property
    def _options(self) -> Mapping[str, Any]:
        """The options as the guided setup has changed them so far."""
        return self._wizard_options if self._wizard_options is not None else self.config_entry.options

    def _start_wizard(self) -> None:
        self._wizard_start = deepcopy(dict(self.config_entry.options))
        self._wizard_options = deepcopy(self._wizard_start)
        self._wizard = list(WIZARD_AREAS)

    def _end_wizard(self) -> None:
        self._wizard = self._wizard_start = self._wizard_options = None

    def _area_menu(self, choices: list[str]) -> list[str]:
        """Return to the profile, or move on through the guided setup."""
        if self._wizard is None:
            return [*choices, "vehicle_profile"]
        return [*choices, "wizard_next", "wizard_finish"]

    async def async_step_wizard_next(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._wizard is None:
            return await self.async_step_init()
        return await self._wizard_advance()

    async def async_step_wizard_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._wizard is None:
            return await self.async_step_init()
        return self._wizard_complete()

    async def _wizard_advance(self) -> ConfigFlowResult:
        self._draft = None
        self._details = {}
        if not self._wizard:
            return self._wizard_complete()
        area = self._wizard.pop(0)
        step: Callable[[], Awaitable[ConfigFlowResult]] = getattr(self, f"async_step_{area}")
        return await step()

    def _wizard_complete(self) -> ConfigFlowResult:
        """Save every change at once, keeping changes made elsewhere meanwhile."""
        # Only the wizard steps call this, and only while it runs.
        start, ours = self._wizard_start or {}, self._wizard_options or {}
        self._end_wizard()
        live = dict(self.config_entry.options)
        result = deepcopy(live)
        for key in start.keys() | ours.keys():
            before, after = start.get(key, _MISSING), ours.get(key, _MISSING)
            if before == after:
                continue
            if live.get(key, _MISSING) not in (before, after):
                return self._options_changed()
            if after is _MISSING:
                result.pop(key, None)
            else:
                result[key] = deepcopy(after)
        return self.async_create_entry(data=result)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        raise NotImplementedError

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        raise NotImplementedError

    def _begin(self, section: str, keys: set[str]) -> OptionsDraft:
        if self._draft is None or self._draft.section != section:
            self._draft = OptionsDraft(
                section, self._options, keys, self._profile_target
            )
        self._details = {}
        return self._draft

    def _target_profile(self) -> VehicleProfile | None:
        key = self._profile_target
        return profiles(self._options).get(key) if key is not None else None

    def _target_ready(self) -> bool:
        """A profile was chosen, or there are no profiles and settings are shared."""
        if CONF_VEHICLE_PROFILES in self._options:
            return self._target_profile() is not None
        return self._profile_target is None

    def _target_options(self) -> dict[str, Any]:
        """The chosen profile's current settings, selected or not."""
        return profile_options(self._options, self._profile_target)

    def _vehicle_placeholders(self) -> dict[str, str]:
        profile = self._target_profile()
        return {"vehicle": profile.name if profile is not None else "—"}

    def _section(self, section: str) -> OptionsDraft | None:
        draft = self._draft
        return draft if draft is not None and draft.section == section else None

    def _vehicle_capabilities(self, values: dict[str, Any]) -> frozenset[Capability]:
        provider, device = values.get(CONF_VEHICLE_PROVIDER), values.get(CONF_VEHICLE_DEVICE)
        if not isinstance(provider, str) or not isinstance(device, str):
            return frozenset()
        identity = values.get("vehicle_source_identity")
        if identity and identity != provider_identity(self.hass, provider, device):
            return frozenset()
        return profile_capabilities(self.hass, provider, device)

    def _suggested_vehicle(
        self, values: dict[str, Any], capability: Capability
    ) -> ProviderSelection:
        own = provider_selection(values, capability)
        if own.provider_id != PROVIDER_NONE:
            return own
        other = provider_selection(
            values, "departure" if capability == "charge_history" else "charge_history"
        )
        if other.provider_id != PROVIDER_NONE and provider_device_is_valid(
            self.hass, other.provider_id, other.device_id
        ):
            return other
        return own

    async def _async_vehicle_provider_step(
        self, *, values: dict[str, Any], capability: Capability, key: str,
        step_id: str, user_input: dict[str, Any] | None,
        next_step: Callable[[], Awaitable[ConfigFlowResult]],
    ) -> ConfigFlowResult:
        """Share provider choice without changing each feature's device validation."""
        providers = [item.id for item in provider_definitions(capability)]
        selection = self._suggested_vehicle(values, capability)
        errors = {}
        if user_input is not None:
            provider = user_input.get(key)
            if provider not in set(providers):
                errors[key] = "vehicle_provider_invalid"
            else:
                self._details["provider"] = provider
                self._details["suggested_device"] = (
                    selection.device_id if selection.provider_id == provider else None
                )
                return await next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vehicle_provider_schema(key, selection.provider_id, providers),
            errors=errors,
        )

    async def _finish_section(self) -> ConfigFlowResult:
        """Commit a completed section after checking concurrent edits."""
        if self._draft is None:
            raise RuntimeError("cannot save an incomplete options section")
        try:
            merged = self._draft.merge(self._options)
        except OptionsChangedError:
            self._end_wizard()
            return self._options_changed()
        self._draft = None
        self._details = {}
        if self._wizard is not None:
            self._wizard_options = merged
            return await self._wizard_advance()
        return self.async_create_entry(data=merged)

    async def async_step_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = None
        self._details = {}
        self._profile_target = None
        self._end_wizard()
        return await self.async_step_init()

    def _options_changed(self) -> ConfigFlowResult:
        return self.async_show_form(
            step_id="options_changed",
            data_schema=probatio.Schema({}),
            errors={"base": "options_changed"},
        )

    async def async_step_options_changed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_back()
        return self._options_changed()
