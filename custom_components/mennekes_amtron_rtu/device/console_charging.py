"""Three charging modes use the existing bus owner and persistent controllers."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from .app_settings import SettingsError
from .console_http import ServiceConsole
from .errors import DeviceError


class ChargingConsole(ServiceConsole):
    """Admin-only choices; no duplicate goals in browser or Supervisor options."""

    def _choices(self) -> dict[str, Any]:
        service = self._configured_service()
        control = service.control
        if control is None:
            raise SettingsError("service_not_configured", 503)
        departure = getattr(service, "departure", None)
        plan = departure.snapshot() if departure is not None and departure.ready else None
        return {
            "schema": 1, "read_only": control.read_only,
            "revision": service.choice_revision,
            "energy": {"target": control.energy_goal.target_kwh, "status": control.energy_goal.status},
            "soc": {
                "configured": control.soc_goal.source is not None,
                "target": control.soc_goal.target_percent,
                "maximum": control.soc_goal.maximum_percent,
                "maximum_required": service.snapshot()["control"]["soc_maximum_required"],
                "status": control.soc_goal.status,
            },
            "departure": None if plan is None or departure is None else {
                "revision": departure.choice_revision,
                "configured": departure.inputs is not None and control.soc_goal.source == departure.inputs.vehicle,
                "deadline": plan["trip"]["deadline"], "status": plan["status"],
                "planned_start": plan["planned_start"], "forecast": plan["forecast"],
                "storage_error": departure.storage_error,
            },
        }

    async def read_charging(self, request: web.Request) -> web.Response:
        user = await self._authorize(request)
        if request.query:
            raise SettingsError("invalid_request")
        await self._configured_service().stable_snapshot()
        return web.json_response({**self._choices(), "csrf": self._digest("csrf", user)})

    async def save_charging(self, request: web.Request) -> web.Response:
        user, payload = await self._edit_request(request)
        if request.query or payload.keys() != {"action", "value", "revision", "confirm"} or payload["confirm"] is not True:
            raise SettingsError("invalid_request")
        service = self._configured_service()
        if not isinstance(payload["revision"], str):
            raise SettingsError("invalid_request")
        action = payload["action"]
        try:
            if action in ("energy", "soc"):
                await service.command({
                    "command": "energy_goal" if action == "energy" else "soc_goal_choice",
                    "value": payload["value"], "expected_choice_revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            elif action == "departure":
                await service.command({
                    "command": "departure_choice", "deadline": payload["value"],
                    "revision": payload["revision"],
                    "expected_serial_number": service.identity.serial_number,
                })
            else:
                raise SettingsError("invalid_request")
        except DeviceError as err:
            # Codes only; never expose source IDs, credentials or arbitrary errors.
            code = str(err)
            safe = code if code in {"soc_goal_source_changed", "soc_goal_limit_unavailable", "read_only_mode"} else "charging_save_uncertain"
            raise SettingsError(safe, 409) from err
        except (ValueError, TypeError, OverflowError) as err:
            raise SettingsError("invalid_request") from err
        return web.json_response({**self._choices(), "csrf": self._digest("csrf", user)})
