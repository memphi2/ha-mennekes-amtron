"""Common draft and explicit navigation for independent options sections."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import probatio
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from ._options_draft import OptionsChangedError, OptionsDraft
from .vehicle_sources import (
    Capability,
    ProviderSelection,
    provider_device_is_valid,
    provider_selection,
)
from .vehicle_sources.const import PROVIDER_NONE


class SectionOptionsFlow(OptionsFlow):
    """Save only a complete section, with real menu routes for edit and back."""

    def __init__(self) -> None:
        super().__init__()
        self._draft: OptionsDraft | None = None
        self._ready = False
        self._details: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        raise NotImplementedError

    def _begin(self, section: str, keys: set[str]) -> OptionsDraft:
        if self._draft is None or self._draft.section != section:
            self._draft = OptionsDraft(section, self.config_entry.options, keys)
        self._ready = False
        self._details = {}
        return self._draft

    def _section(self, section: str) -> OptionsDraft | None:
        draft = self._draft
        return draft if draft is not None and draft.section == section else None

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

    def _review(self) -> ConfigFlowResult:
        self._ready = True
        return self.async_show_menu(
            step_id="review", menu_options=["save", "edit", "back"]
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._draft is None or not self._ready:
            return await self.async_step_init()
        return self._review()

    async def async_step_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._draft = None
        self._ready = False
        self._details = {}
        return await self.async_step_init()

    async def async_step_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._draft is None:
            return await self.async_step_init()
        # Sections are named by our fixed root menu, never by user text.
        step = cast(
            Callable[[], Awaitable[ConfigFlowResult]],
            getattr(self, f"async_step_{self._draft.section}"),
        )
        return await step()

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._draft is None or not self._ready:
            return await self.async_step_init()
        try:
            merged = self._draft.merge(self.config_entry.options)
        except OptionsChangedError:
            return await self.async_step_options_changed()
        self._draft = None
        self._ready = False
        return self.async_create_entry(data=merged)

    async def async_step_options_changed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_back()
        return self.async_show_form(
            step_id="options_changed",
            data_schema=probatio.Schema({}),
            errors={"base": "options_changed"},
        )
