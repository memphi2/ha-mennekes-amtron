"""One guest-start action, using limits already held by the service."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .control import ha_command
from .device.client_errors import AmtronConnectionError
from .device.errors import DeviceError
from .remote import RemoteClient


class ServiceOrders:
    """Read current guest state and explicitly start the connected guest."""

    def __init__(self, client: RemoteClient, refresh: Callable[[], Awaitable[None]]) -> None:
        self._client = client
        self._refresh = refresh

    @property
    def jobs(self) -> dict[str, Any] | None:
        control = self._client.snapshot.get("control")
        status = control.get("orders") if isinstance(control, dict) else None
        return status if isinstance(status, dict) else None

    @ha_command
    async def async_start_guest(self) -> None:
        client = self._client
        if not client.connected:
            raise DeviceError("service_unavailable")
        try:
            await client.update()
        except AmtronConnectionError as err:
            raise DeviceError("service_unavailable") from err
        snapshot = client.snapshot
        revision = snapshot.get("control", {}).get("guest_revision")
        try:
            await client.execute("guest_start", expected_guest_revision=revision)
        finally:
            await self._refresh()
