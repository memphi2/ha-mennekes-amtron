"""Transactional adapter changes during explicit Home Assistant setup only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigFlowResult

from .device.waveshare_gateway import (
    GatewaySettings,
    GatewaySnapshot,
    configure_gateway,
)


@dataclass(frozen=True, slots=True)
class GatewayChange:
    """Apply a previewed adapter configuration around the final wallbox check."""

    host: str
    snapshot: GatewaySnapshot
    settings: GatewaySettings
    password: str = field(repr=False)

    async def async_apply(
        self, finish: Callable[[], Awaitable[ConfigFlowResult]],
    ) -> ConfigFlowResult:
        async with await configure_gateway(
            self.host, self.settings, password=self.password, expected=self.snapshot,
        ) as receipt:
            result = await finish()
            if result["type"] == "create_entry" or (
                result["type"] == "abort" and result.get("reason") == "reconfigure_successful"
            ):
                # HA persists a new entry after accepting this return value.
                # Identity and duplicate checks have completed at this point.
                await receipt.commit()
            return result
