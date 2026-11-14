"""The app's charging jobs, boost, guest charging and weekly plans, from HA.

The app asks for a preview before it accepts an order: the preview carries the
checked values and is bound to the current plug-in. An action here asks for
that preview and confirms it at once, so a dashboard needs one tap and an
automation one call. Whoever taps confirms in the dashboard's own prompt.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .control import ha_command
from .device.client_errors import AmtronConnectionError
from .device.errors import DeviceError, DeviceValidationError
from .remote import RemoteClient

JOBS = "/v2/jobs"
WEEKLY = "/v2/weekly"


class ServiceOrders:
    """The last order status of the app, and the actions that change it."""

    def __init__(self, client: RemoteClient, notify: Callable[[], None]) -> None:
        self._client = client
        self._notify = notify
        # None: not read yet, or this app version has no such page.
        self.jobs: dict[str, Any] | None = None
        self.weekly: dict[str, Any] | None = None

    async def async_refresh(self) -> None:
        """Read both statuses; a failure only empties them."""
        for name, path in (("jobs", JOBS), ("weekly", WEEKLY)):
            try:
                value: dict[str, Any] | None = await self._client.request(
                    "GET", path, update_connection=False
                )
            except (AmtronConnectionError, DeviceError):
                value = None
            setattr(self, name, value)

    async def _call(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return await self._client.request(
                "GET" if payload is None else "POST", path, payload, update_connection=False
            )
        except AmtronConnectionError as err:
            # Unreachable, too old for this action, or values it cannot use.
            raise DeviceError("service_order_failed") from err

    async def _done(self) -> None:
        await self.async_refresh()
        self._notify()

    async def _create(self, action: str, request: dict[str, Any]) -> None:
        state = await self._call(JOBS)
        preview = await self._call(JOBS, {
            "action": action, "revision": state.get("revision"), "scope": state.get("scope"),
            "request": {"id": secrets.token_hex(16), **request},
        })
        try:
            await self._call(JOBS, {
                "action": "create", "revision": preview.get("revision"),
                "scope": preview.get("scope"), "request": preview.get("request"),
            })
        finally:
            await self._done()

    async def _confirm(self, path: str, action: str) -> None:
        state = await self._call(path)
        try:
            await self._call(path, {"action": action, "revision": state.get("revision"), "confirm": True})
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
        await self._confirm(JOBS, "stop")

    @ha_command
    async def async_clear_job(self) -> None:
        await self._confirm(JOBS, "clear")

    @ha_command
    async def async_start_boost(self, *, minutes: int) -> None:
        state = await self._call(JOBS)
        preview = await self._call(JOBS, {
            "action": "preview_boost", "revision": state.get("revision"),
            "scope": state.get("scope"), "minutes": minutes,
        })
        try:
            await self._call(JOBS, {
                "action": "boost", "revision": preview.get("revision"),
                "scope": preview.get("scope"), "until": preview.get("until"),
            })
        finally:
            await self._done()

    @ha_command
    async def async_cancel_boost(self) -> None:
        await self._confirm(JOBS, "cancel_boost")

    @ha_command
    async def async_activate_weekly(self, *, plan: str) -> None:
        state = await self._call(WEEKLY)
        wanted = plan.strip().casefold()
        found = None
        for item in state.get("plans") or ():
            recipe = item.get("plan") if isinstance(item, dict) else None
            if isinstance(recipe, dict) and wanted in (
                str(recipe.get("vehicle_profile", "")).strip().casefold(), str(recipe.get("id", "")).casefold(),
            ):
                found = recipe
                break
        if found is None:
            raise DeviceValidationError("weekly_plan_unknown", {"plan": plan})
        proposal = await self._call(WEEKLY, {
            "action": "preview_activation", "revision": state.get("revision"),
            "plan_id": found.get("id"), "vehicle_profile": found.get("vehicle_profile"),
            "scope": state.get("scope"),
        })
        try:
            await self._call(WEEKLY, {"action": "activate", "proposal": proposal, "confirm": True})
        finally:
            await self._done()

    @ha_command
    async def async_deactivate_weekly(self) -> None:
        await self._confirm(WEEKLY, "deactivate")
