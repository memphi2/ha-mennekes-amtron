"""Common draft and concurrent-edit protection for options sections."""

from __future__ import annotations

from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from ._options_draft import OptionsChangedError, OptionsDraft
from .vehicle_profiles import CONF_VEHICLE_DEVICE, CONF_VEHICLE_PROVIDER
from .vehicle_sources import (
    Capability,
    ProviderSelection,
    profile_capabilities,
    provider_device_is_valid,
    provider_identity,
    provider_selection,
)
from .vehicle_sources.const import PROVIDER_NONE


class SectionOptionsFlow(OptionsFlow):
    """Save only a complete section, preserving changes in other sections."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: OptionsDraft | None = None
        self._details: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        raise NotImplementedError

    def _begin(self, section: str, keys: set[str]) -> OptionsDraft:
        if self._draft is None or self._draft.section != section:
            self._draft = OptionsDraft(section, self.config_entry.options, keys)
        self._details = {}
        return self._draft

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

    def _finish_section(self) -> ConfigFlowResult:
        """Commit a completed section after checking concurrent edits."""
        if self._draft is None:
            raise RuntimeError("cannot save an incomplete options section")
        try:
            merged = self._draft.merge(self.config_entry.options)
        except OptionsChangedError:
            return self._options_changed()
        self._draft = None
        self._details = {}
        return self.async_create_entry(data=merged)

    async def async_step_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = None
        self._details = {}
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
