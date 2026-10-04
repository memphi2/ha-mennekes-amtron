"""Shared Home Assistant config-entry types for MENNEKES AMTRON."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .data import MennekesAmtronRuntimeData

    type MennekesAmtronConfigEntry = ConfigEntry[MennekesAmtronRuntimeData]
else:
    MennekesAmtronConfigEntry = Any
