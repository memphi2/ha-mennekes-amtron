"""Home Assistant client for the independent Modbus service.

Disconnecting this client never stops the server or writes a charging command.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.helpers.update_coordinator import UpdateFailed

from .control import AmtronControl, ControlOptions, ha_command
from .coordinator import MennekesAmtronCoordinator
from .device.client import MennekesModbusClient, SerialConfig
from .device.client_errors import AmtronConnectionError, AmtronWriteRejectedError
from .device.data import DeviceIdentity, WallboxData, WriteDiagnostics
from .device.decode import RegisterValue
from .device.enums import RequestedPhases, SolarChargingMode
from .device.errors import DeviceError, DeviceValidationError
from .device.heartbeat import HeartbeatTask
from .device.identity import usable_max_evse_current
from .device.register_blocks import REGISTER_BLOCKS
from .device.registers import ERROR_CODE, RegisterSpec
from .remote_errors import ServiceConnectionError

CONF_SERVER_URL = "server_url"
CONF_SERVER_TOKEN = "server_token"  # noqa: S105 - configuration key, not a credential


def server_url(value: str) -> str:
    """Accept a base URL, never embedded credentials, queries or fragments."""
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("invalid service URL")
    # Validate malformed or out-of-range port numbers before storing the URL.
    _ = parts.port
    return value.strip().rstrip("/")


class RemoteClient(MennekesModbusClient):
    """Read service snapshots; no local serial connection or write tunnel."""

    def __init__(self, session: ClientSession, url: str, token: str) -> None:
        super().__init__(SerialConfig(port="remote-service"))
        self._session = session
        self._url = server_url(url)
        self._token = token
        self._connected = False
        self.snapshot: dict[str, Any] = {}
        self.identity = DeviceIdentity()
        self.on_update: Callable[[], None] | None = None
        self._snapshot_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_connect(self) -> None:
        await self.update()

    async def async_close(self) -> None:
        self._connected = False

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None,
        *, update_connection: bool = True,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                self._url + path,
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload,
                timeout=ClientTimeout(total=15),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise ServiceConnectionError("service_auth_failed", "service authentication failed")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 131072:
                        raise ServiceConnectionError("service_invalid_response", "service response too large")
                import json

                try:
                    data = json.loads(raw)
                except ValueError as err:
                    raise ServiceConnectionError("service_invalid_response", "service response is not JSON") from err
                if not isinstance(data, dict):
                    raise ServiceConnectionError("service_invalid_response", "service response is not an object")
                if response.status == 409:
                    key = data.get("error")
                    placeholders = data.get("placeholders")
                    if not isinstance(key, str) or (
                        placeholders is not None
                        and (
                            not isinstance(placeholders, dict)
                            or not all(
                                isinstance(k, str) and isinstance(v, str)
                                for k, v in placeholders.items()
                            )
                        )
                    ):
                        raise ValueError("invalid command error")
                    error = (
                        DeviceValidationError
                        if data.get("validation") is True
                        else DeviceError
                    )
                    raise error(key, placeholders)
                if response.status != 200:
                    raise AmtronConnectionError("service request failed")
                return data
        except (ClientError, TimeoutError, ValueError, AmtronConnectionError) as err:
            if update_connection:
                self._connected = False
                if self.on_update is not None:
                    self.on_update()
            if isinstance(err, ServiceConnectionError):
                raise
            raise AmtronConnectionError(
                "cannot communicate with the Modbus service"
            ) from err

    async def update(self) -> WallboxData:
        async with self._snapshot_lock:
            payload = await self.request("GET", "/v1/state")
            return self.apply_snapshot(payload)

    def apply_snapshot(self, payload: dict[str, Any]) -> WallboxData:
        section = "api_version"
        try:
            if type(payload.get("api_version")) is not int or payload["api_version"] != 1:
                raise ValueError("unsupported API version")
            section = "values"
            values = payload["values"]
            control = payload["control"]
            if not isinstance(values, dict) or not all(
                isinstance(k, str)
                and (
                    v is None
                    or isinstance(v, str)
                    or type(v) is int
                    or (type(v) is float and math.isfinite(v))
                )
                for k, v in values.items()
            ):
                raise ValueError("invalid values")
            section = "control"
            if not isinstance(control, dict) or not isinstance(
                control.get("read_only"), bool
            ):
                raise ValueError("invalid controls")
            section = "identity"
            identity = DeviceIdentity(**payload["identity"])
            if (
                type(identity.layout_version) is not int
                or not 1 <= identity.layout_version <= 65535
            ):
                raise ValueError("invalid identity layout")
            for text in (
                identity.firmware_version,
                identity.serial_number,
                identity.article_number,
            ):
                if text is not None and (not isinstance(text, str) or len(text) > 128):
                    raise ValueError("invalid identity text")
            section = "identity.max_evse_current"
            if identity.max_evse_current is not None and (
                type(identity.max_evse_current) not in (float, int)
                or (
                    type(identity.max_evse_current) is float
                    and not math.isfinite(identity.max_evse_current)
                )
            ):
                raise ValueError("invalid device current")
            # Older service snapshots contain the raw optional register value.
            # An unusable capability must not invalidate authenticated readings;
            # command limits below remain mandatory and strictly validated.
            identity.max_evse_current = usable_max_evse_current(identity.max_evse_current)
            section = "identity.phase_options_hw"
            if identity.phase_options_hw is not None and (
                type(identity.phase_options_hw) is not int
                or not 0 <= identity.phase_options_hw <= 65535
            ):
                raise ValueError("invalid device phases")
            section = "control.limits"
            for key in ("max_charging_current", "current_cap"):
                value = control.get(key)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 6 <= value <= 32
                ):
                    raise ValueError("invalid service limits")
            section = "heartbeat"
            heartbeat = payload["heartbeat"]
            if (
                not isinstance(heartbeat, dict)
                or type(heartbeat.get("running")) is not bool
                or (
                    heartbeat.get("healthy") is not None
                    and type(heartbeat["healthy"]) is not bool
                )
            ):
                raise ValueError("invalid heartbeat state")
            section = "diagnostics"
            diagnostics = payload["diagnostics"]
            if not isinstance(diagnostics, dict) or not all(
                type(value) is int and value >= 0 for value in diagnostics.values()
            ):
                raise ValueError("invalid diagnostics")
            section = "write_errors"
            errors = payload["write_errors"]
            if not isinstance(errors, list) or not all(
                isinstance(value, str) for value in errors
            ):
                raise ValueError("invalid write errors")
            section = "control.intent"
            for key in ("pause_requested", "charging_release_requested"):
                value = control.get(key)
                if value is not None and not isinstance(value, bool):
                    raise ValueError("invalid intent")
            current = control.get("charging_setpoint")
            if current is not None and (
                type(current) not in (int, float)
                or not (current == 0 or 6 <= current <= 32)
            ):
                raise ValueError("invalid setpoint")
            section = "failed_blocks"
            failed = payload["failed_blocks"]
            if not isinstance(failed, list) or not all(
                isinstance(item, str) for item in failed
            ):
                raise ValueError("invalid failed blocks")
            section = "current_write_generation"
            generation = payload["current_write_generation"]
            if type(generation) is not int or generation < 0:
                raise ValueError("invalid generation")
        except (KeyError, TypeError, ValueError) as err:
            self._connected = False
            if self.on_update is not None:
                self.on_update()
            raise ServiceConnectionError(
                "service_invalid_response", f"invalid service snapshot ({section})"
            ) from err
        self.identity = identity
        self.snapshot = payload
        self._connected = True
        self.write_state.pause_requested = control.get("pause_requested")
        self.write_state.charging_release_requested = control.get(
            "charging_release_requested"
        )
        self.write_state.charging_setpoint = current
        self.write_state.current_write_generation = generation
        self.write_state.confirmed_current = values.get("charging_current_ems")
        if self.on_update is not None:
            self.on_update()
        if payload.get("available") is not True:
            raise ServiceConnectionError("service_not_ready", "wallbox is unavailable at the service")
        return WallboxData(
            values=values,
            failed_blocks=tuple(failed),
            current_write_generation=generation,
            service_read_only=control["read_only"],
            service_max_charging_current=control["max_charging_current"],
            service_charging_setpoint=current,
        )

    async def execute(self, command: str, **values: Any) -> None:
        # Deliberately no retries: a timed-out command may have reached the box.
        try:
            # A delayed poll response must not overwrite the command's newer
            # intent/readback. Serialize request and publication together.
            async with self._snapshot_lock:
                self.apply_snapshot(
                    await self.request(
                        "POST", "/v1/command", {"command": command, **values}
                    )
                )
        except AmtronConnectionError as err:
            raise DeviceError("service_unavailable") from err

    async def async_read_register(self, spec: RegisterSpec) -> RegisterValue:
        if spec == ERROR_CODE:
            data = await self.request("POST", "/v1/read", {"register": spec.key})
            value = data.get("value")
            if type(value) is not int:
                raise AmtronConnectionError("invalid error register")
            return value
        value = self.snapshot.get("values", {}).get(spec.key)
        return value if isinstance(value, (int, float, str)) else None

    async def async_read_words(self, *, address: int, count: int) -> list[int]:
        block = next(
            (
                block
                for block in REGISTER_BLOCKS
                if block.address == address and block.count == count
            ),
            None,
        )
        if block is None:
            raise AmtronConnectionError("unsupported diagnostic block")
        response = await self.request("POST", "/v1/read", {"block": block.name})
        words = response.get("words")
        if (
            not isinstance(words, list)
            or len(words) != count
            or not all(type(word) is int and 0 <= word <= 65535 for word in words)
        ):
            raise AmtronConnectionError("invalid register image")
        return words

    async def async_write_register(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
        priority: bool = False,
    ) -> None:
        raise AmtronWriteRejectedError(
            "direct register writes are not available through the service"
        )


class RemoteHeartbeat(HeartbeatTask):
    """Report server heartbeat health without creating a local sender."""

    def __init__(self, client: RemoteClient, diagnostics: WriteDiagnostics) -> None:
        super().__init__(client, diagnostics)
        self._remote = client
        self.update()

    @property
    def running(self) -> bool:
        return (
            self._remote.connected
            and self._remote.snapshot.get("heartbeat", {}).get("running") is True
        )

    def update(self) -> None:
        value = self._remote.snapshot.get("heartbeat", {}).get("healthy")
        self._set_healthy(
            value if self._remote.connected and isinstance(value, bool) else None
        )


class RemoteCoordinator(MennekesAmtronCoordinator):
    """Poll the service's cache; the service independently schedules bus reads."""

    async def _async_update_data(self) -> WallboxData:
        if not isinstance(self._client, RemoteClient):
            raise UpdateFailed("service client missing")
        try:
            return await self._client.update()
        except AmtronConnectionError as err:
            raise UpdateFailed(str(err)) from err


class RemoteControl(AmtronControl):
    """Commands are validated by the server that owns current and pause state."""

    def __init__(
        self,
        client: RemoteClient,
        *,
        diagnostics: WriteDiagnostics,
        options: ControlOptions,
        state: Callable[[], WallboxData],
        request_refresh: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(
            client,
            diagnostics=diagnostics,
            identity=client.identity,
            options=options,
            state=state,
            request_refresh=request_refresh,
        )
        self._remote = client

    @property
    def read_only(self) -> bool:
        return (
            not self._remote.connected
            or self._remote.snapshot.get("available") is not True
            or self._remote.snapshot.get("control", {}).get("read_only") is not False
        )

    def _require_write_access(self) -> None:
        """Use current service permission, never a former HA-only setting."""
        if not self._remote.connected or self._remote.snapshot.get("available") is not True:
            raise DeviceError("service_unavailable")
        if self.read_only:
            raise DeviceValidationError("read_only_mode")

    @property
    def max_charging_current(self) -> float:
        maximum = self._remote.snapshot.get("control", {}).get(
            "max_charging_current", 6
        )
        return min(super().max_charging_current, float(maximum))

    @property
    def charging_setpoint(self) -> float | None:
        return self._write_state.charging_setpoint

    @charging_setpoint.setter
    def charging_setpoint(self, value: float | None) -> None:
        """HA restore data must not overwrite the independent service."""

    @property
    def pause_requested(self) -> bool | None:
        return self._write_state.pause_requested

    @pause_requested.setter
    def pause_requested(self, value: bool | None) -> None:
        """The server remains authoritative across HA restarts."""

    @property
    def charging_release_requested(self) -> bool | None:
        return self._write_state.charging_release_requested

    @charging_release_requested.setter
    def charging_release_requested(self, value: bool | None) -> None:
        """The server remains authoritative across HA restarts."""

    async def _command(self, command: str, **values: Any) -> None:
        self._require_write_access()
        await self._remote.execute(command, **values)
        if self._request_refresh is not None:
            await self._request_refresh()

    @ha_command
    async def async_set_charging_current(
        self, value: float, *, allow_unlimited: bool = False
    ) -> None:
        if not math.isfinite(value):
            raise DeviceValidationError(
                "charging_current_invalid", {"value": str(value), "minimum": "6"}
            )
        await self._command(
            "set_current",
            value=min(value, self.max_charging_current),
            allow_unlimited=allow_unlimited,
        )

    @ha_command
    async def async_pause_charging(self) -> None:
        await self._command("pause")

    @ha_command
    async def async_resume_charging(self, *, current_cap: float | None = None) -> None:
        await self._command(
            "resume", current_cap=self._resume_current_maximum(current_cap)
        )

    @ha_command
    async def async_set_charging_release(self, released: bool) -> None:
        await self._command("release", value=released)

    @ha_command
    async def async_set_lock_evse(self, locked: bool) -> None:
        await self._command("lock", value=locked)

    @ha_command
    async def async_set_solar_charging_mode(self, mode: SolarChargingMode) -> None:
        await self._command("solar", value=int(mode))

    @ha_command
    async def async_set_requested_phases(self, phases: RequestedPhases) -> None:
        await self._command("phases", value=int(phases))

    @ha_command
    async def async_recover_from_error(self) -> bool:
        await self._command("recover")
        return True

    @ha_command
    async def async_restart(self) -> None:
        await self._command("restart")
