"""The app's charging jobs, boost and guest charging, from HA.

The app asks for a preview before it accepts an order: the preview carries the
checked values and is bound to the current plug-in. An action here asks for
that preview and confirms it at once, so a dashboard needs one tap and an
automation one call. Whoever taps confirms in the dashboard's own prompt.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .control import ha_command
from .device.client_errors import AmtronConnectionError
from .device.decode import is_hex32
from .device.errors import DeviceError
from .device.job_draft import DRAFT_STATES, validate
from .remote import RemoteClient
from .remote_errors import ServiceConnectionError

JOBS = "/v2/jobs"


class ServiceOrders:
    """The last order status of the app, and the actions that change it."""

    def __init__(self, client: RemoteClient, refresh: Callable[[], Awaitable[None]]) -> None:
        self._client = client
        self._refresh = refresh

    @property
    def jobs(self) -> dict[str, Any] | None:
        """The job status from the app's last state; None while it has none."""
        control = self._client.snapshot.get("control")
        status = control.get("orders") if isinstance(control, dict) else None
        return status if isinstance(status, dict) else None

    @property
    def draft(self) -> dict[str, Any] | None:
        status = self.jobs
        draft = status.get("draft") if status is not None else None
        if not isinstance(draft, dict) or not is_hex32(draft.get("revision")) or type(draft.get("storage_error")) is not bool or draft.get("status") not in DRAFT_STATES:
            return None
        try:
            validate(draft.get("values"))
        except (ValueError, TypeError, OverflowError):
            return None
        return draft

    @ha_command
    async def async_edit_draft(self, field: str, value: object) -> None:
        draft = self.draft
        if draft is None:
            raise DeviceError("service_order_failed")
        try:
            await self._call({"action": "edit_draft", "draft_revision": draft.get("revision"), "changes": {field: value}})
        finally:
            await self._done()

    @ha_command
    async def async_reset_draft(self) -> None:
        draft = self.draft
        if draft is None:
            raise DeviceError("service_order_failed")
        try:
            await self._call({"action": "reset_draft", "draft_revision": draft.get("revision"), "confirm": True})
        finally:
            await self._done()

    @ha_command
    async def async_start_draft(self) -> None:
        draft = self.draft
        if draft is None:
            raise DeviceError("service_order_failed")
        state = await self._call()
        try:
            preview = await self._call({
                "action": "preview_draft", "draft_revision": draft.get("revision"),
                "revision": state.get("revision"), "scope": state.get("scope"),
            })
            await self._call({
                "action": "create_draft", "draft_revision": preview.get("draft_revision"),
                "revision": preview.get("revision"), "scope": preview.get("scope"), "request": preview.get("request"),
            })
        finally:
            await self._done()

    async def _call(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._client
        if payload is not None:
            # As for every other command: only while the app answers for the
            # wallbox this entry is bound to, and the app checks it again.
            if not client.connected:
                raise DeviceError("service_unavailable")
            payload = {**payload, "expected_serial_number": client.bound_serial}
        try:
            return await client.request(
                "GET" if payload is None else "POST", JOBS, payload, update_connection=False
            )
        except ServiceConnectionError as err:
            if err.reason == "service_identity_changed":
                raise DeviceError("device_identity_changed") from err
            raise DeviceError("service_order_failed") from err
        except AmtronConnectionError as err:
            # Unreachable, too old for this action, or values it cannot use.
            raise DeviceError("service_order_failed") from err

    async def _done(self) -> None:
        # One read of the app's state shows the order and what it changed.
        await self._refresh()

    async def _create(self, action: str, request: dict[str, Any]) -> None:
        state = await self._call()
        preview = await self._call({
            "action": action, "revision": state.get("revision"), "scope": state.get("scope"),
            "request": {"id": secrets.token_hex(16), **request},
        })
        try:
            await self._call({
                "action": "create", "revision": preview.get("revision"),
                "scope": preview.get("scope"), "request": preview.get("request"),
            })
        finally:
            await self._done()

    async def _confirm(self, action: str) -> None:
        state = await self._call()
        try:
            await self._call({"action": action, "revision": state.get("revision"), "confirm": True})
        finally:
            await self._done()

    @ha_command
    async def async_start_job(
        self, *, start: datetime, finish: datetime, current: float, energy: float, reserve: float = 0,
    ) -> None:
        await self._create("preview", {
            "start_at": start.isoformat(), "finish_at": finish.isoformat(),
            "current_a": current, "energy_kwh": energy,
            **({"reserve_kwh": reserve} if reserve > 0 else {}),
        })

    @ha_command
    async def async_start_guest(self, *, minutes: int, current: float, energy: float) -> None:
        await self._create("preview_guest", {
            "duration_minutes": minutes, "current_a": current, "energy_kwh": energy,
        })

    @ha_command
    async def async_stop_job(self) -> None:
        await self._confirm("stop")

    @ha_command
    async def async_clear_job(self) -> None:
        await self._confirm("clear")

    @ha_command
    async def async_start_boost(self, *, minutes: int) -> None:
        state = await self._call()
        preview = await self._call({
            "action": "preview_boost", "revision": state.get("revision"),
            "scope": state.get("scope"), "minutes": minutes,
        })
        try:
            await self._call({
                "action": "boost", "revision": preview.get("revision"),
                "scope": preview.get("scope"), "until": preview.get("until"),
            })
        finally:
            await self._done()

    @ha_command
    async def async_cancel_boost(self) -> None:
        await self._confirm("cancel_boost")
