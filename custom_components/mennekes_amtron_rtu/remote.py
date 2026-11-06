"""Home Assistant client for the independent Modbus service.

Disconnecting this client never stops the server or writes a charging command.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from .control import AmtronControl, ControlOptions, ha_command
from .coordinator import MennekesAmtronCoordinator
from .device.charging_session import CompletedSession
from .device.client import MennekesModbusClient, SerialConfig
from .device.client_errors import AmtronConnectionError, AmtronWriteRejectedError
from .device.command_events import CommandEvents
from .device.const import (
    DOMAIN,
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
)
from .device.control import PENDING_ACTIONS
from .device.data import DeviceIdentity, WallboxData, WriteDiagnostics
from .device.decode import RegisterValue
from .device.energy_goal import EnergyGoal
from .device.enums import RequestedPhases, SolarChargingMode
from .device.errors import DeviceError, DeviceValidationError
from .device.heartbeat import HeartbeatTask
from .device.identity import usable_max_evse_current
from .device.link_quality import CONNECTION_QUALITY_STATES
from .device.register_blocks import REGISTER_BLOCKS
from .device.registers import ERROR_CODE, RegisterSpec
from .device.soc_goal import SocGoal
from .remote_compat import SUPPORTED_API_VERSION, validate_service_version
from .remote_errors import ServiceConnectionError, ServiceFailure

CONF_SERVER_URL = "server_url"
CONF_SERVER_TOKEN = "server_token"  # noqa: S105 - configuration key, not a credential


def _pending_status(control: dict[str, Any]) -> tuple[str | None, datetime | None]:
    """Validate optional API 2 queue metadata without rejecting older services."""
    if "pending_action" not in control and "pending_until" not in control:
        return None, None
    action = control["pending_action"]
    until = control["pending_until"]
    if action is None and until is None:
        return None, None
    if not isinstance(action, str) or action not in PENDING_ACTIONS:
        raise ValueError("invalid pending action")
    if action in ("single_phase", "all_phases") and until is None:
        # The car has not acknowledged its pause yet; no execution time is
        # known. The timestamp appears once the additional grace starts.
        return action, None
    if not isinstance(until, str) or len(until) > 40:
        raise ValueError("invalid pending deadline")
    deadline = datetime.fromisoformat(until)
    if deadline.tzinfo is None:
        raise ValueError("pending deadline must include a timezone")
    return action, deadline.astimezone(UTC)


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
        self._bound_serial: str | None = None
        self.failure_reason: ServiceFailure | None = None
        self.on_update: Callable[[], None] | None = None
        self.on_auth_failed: Callable[[], None] | None = None
        self._snapshot_lock = asyncio.Lock()
        self.pending_until: datetime | None = None
        self.events = CommandEvents(supported=False)
        self._last_energy_observed_at: float | None = None

    def bind_identity(self, serial_number: str | None) -> None:
        """Bind a configured client; setup probes remain free to read identity."""
        if self._bound_serial is not None and self._bound_serial != serial_number:
            raise ValueError("service identity is already bound")
        self._bound_serial = serial_number

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_connect(self) -> None:
        await self.update()

    async def async_close(self) -> None:
        self._connected = False
        self.on_auth_failed = None

    def _mark_unavailable(self, reason: ServiceFailure | None = None) -> None:
        """Publish a failed connection immediately, including command failures."""
        self.failure_reason = reason
        self._connected = False
        if self.on_update is not None:
            self.on_update()

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
                if response.status == 404 and path == "/v2/state":
                    raise ServiceConnectionError("service_version_mismatch", "service API version differs")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    if len(raw) > 131072:
                        raise ServiceConnectionError("service_invalid_response", "service response too large")
                import json

                try:
                    data = json.loads(raw)
                except (ValueError, RecursionError) as err:
                    raise ServiceConnectionError("service_invalid_response", "service response is not JSON") from err
                if not isinstance(data, dict):
                    raise ServiceConnectionError("service_invalid_response", "service response is not an object")
                if response.status == 409:
                    key = data.get("error")
                    if key == "device_identity_changed":
                        raise ServiceConnectionError(
                            "service_identity_changed", "service identity differs from the configured wallbox"
                        )
                    if key == "service_unavailable":
                        raise ServiceConnectionError(
                            "service_not_ready", "wallbox is unavailable at the service"
                        )
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
                    if key == "read_only_mode":
                        # The service changed policy after our last snapshot.
                        # Block cached controls until the next normal poll;
                        # never retry the command or fetch in its error path.
                        self._mark_unavailable("service_not_ready")
                    raise error(key, placeholders)
                if response.status != 200:
                    raise AmtronConnectionError("service request failed")
                return data
        except (ClientError, TimeoutError, ValueError, AmtronConnectionError) as err:
            authentication_failed = (
                isinstance(err, ServiceConnectionError)
                and err.reason == "service_auth_failed"
            )
            if update_connection or authentication_failed:
                self._mark_unavailable(err.reason if isinstance(err, ServiceConnectionError) else None)
            if isinstance(err, ServiceConnectionError):
                if authentication_failed and self.on_auth_failed is not None:
                    self.on_auth_failed()
                raise
            raise AmtronConnectionError(
                "cannot communicate with the Modbus service"
            ) from err

    async def update(self) -> WallboxData:
        async with self._snapshot_lock:
            payload = await self.request("GET", "/v2/state")
            return self.apply_snapshot(payload)

    def apply_snapshot(
        self, payload: dict[str, Any], *, require_available: bool = True,
        consume_energy: bool = True,
    ) -> WallboxData:
        section = "api_version"
        try:
            if type(payload.get("api_version")) is not int:
                raise ValueError("unsupported API version")
            if payload["api_version"] != SUPPORTED_API_VERSION:
                raise ServiceConnectionError("service_version_mismatch", "service API version differs")
            section = "service_version"
            validate_service_version(payload)
            section = "command_events"
            command_events = CommandEvents.parse(payload.get("command_events"))
            section = "connection_quality"
            quality = payload.get("connection_quality")
            if quality is not None and quality not in CONNECTION_QUALITY_STATES:
                raise ValueError("invalid connection quality")
            section = "charging_session"
            session_supported = False
            completed_session = None
            if "charging_session" in payload:
                summary = payload["charging_session"]
                if (
                    not isinstance(summary, dict)
                    or summary.keys() != {"supported", "latest"}
                    or type(summary["supported"]) is not bool
                    or (not summary["supported"] and summary["latest"] is not None)
                ):
                    raise ValueError("invalid charging session summary")
                session_supported = summary["supported"]
                if summary["latest"] is not None:
                    completed_session = CompletedSession.parse(summary["latest"])
            section = "energy_sample"
            observed_at: float | None = None
            observed_energy: float | None = None
            sample = payload.get("energy_sample")
            if sample is not None:
                if not isinstance(sample, dict) or sample.keys() != {"observed_at", "value"}:
                    raise ValueError("invalid energy sample")
                stamp, energy = sample["observed_at"], sample["value"]
                if type(stamp) not in (int, float) or not 0 <= stamp <= 253402300799:
                    raise ValueError("invalid meter observation timestamp")
                if energy is not None and (
                    type(energy) not in (int, float) or not math.isfinite(energy) or energy < 0
                ):
                    raise ValueError("invalid meter observation value")
                observed_at = float(stamp)
                observed_energy = float(energy) if energy is not None else None
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
            section = "control.pending_change"
            pending_action, pending_until = _pending_status(control)
            section = "control.supports_cancel_pending"
            if not isinstance(control.get("supports_cancel_pending", False), bool):
                raise ValueError("invalid cancellation capability")
            section = "control.energy_goal"
            goal_supported = control.get("supports_energy_goal", False)
            if type(goal_supported) is not bool:
                raise ValueError("invalid energy goal capability")
            goal_raw = control.get("energy_goal")
            goal = EnergyGoal.restore(goal_raw) if goal_raw is not None else EnergyGoal()
            if goal_supported and goal_raw is None:
                raise ValueError("missing energy goal state")
            section = "control.soc_goal"
            soc_supported = control.get("supports_soc_goal", False)
            if type(soc_supported) is not bool:
                raise ValueError("invalid percentage goal capability")
            soc_raw = control.get("soc_goal")
            soc_goal = SocGoal.restore(soc_raw) if soc_raw is not None else SocGoal()
            if soc_supported and soc_raw is None:
                raise ValueError("missing percentage goal state")
            section = "control.state_change_interval_seconds"
            interval = control.get("state_change_interval_seconds")
            if interval is not None and (
                type(interval) not in (int, float)
                or not 0 <= interval <= MAX_STATE_CHANGE_INTERVAL_SECONDS
            ):
                raise ValueError("invalid state-change interval")
            section = "control.phase_switch_grace_seconds"
            if "phase_switch_grace_seconds" in control:
                grace = control["phase_switch_grace_seconds"]
                if (
                    type(grace) not in (int, float)
                    or not 0 <= grace <= MAX_PHASE_SWITCH_GRACE_SECONDS
                    or int(grace) != grace
                ):
                    raise ValueError("invalid phase-switch grace time")
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
        except ServiceConnectionError as err:
            self._mark_unavailable(err.reason)
            raise
        except (KeyError, TypeError, ValueError, OverflowError) as err:
            self._mark_unavailable("service_invalid_response")
            raise ServiceConnectionError(
                "service_invalid_response", f"invalid service snapshot ({section})"
            ) from err
        if self._bound_serial is not None and identity.serial_number != self._bound_serial:
            self._mark_unavailable("service_identity_changed")
            raise ServiceConnectionError(
                "service_identity_changed", "service identity differs from the configured wallbox"
            )
        try:
            self.events.receive(command_events, connected=self._connected)
        except ValueError as err:
            self._mark_unavailable("service_invalid_response")
            raise ServiceConnectionError(
                "service_invalid_response", "invalid service snapshot (command_events)"
            ) from err
        self.identity = identity
        self.snapshot = payload
        self.pending_until = pending_until
        self.failure_reason = None
        self._connected = True
        self.write_state.pause_requested = control.get("pause_requested")
        self.write_state.charging_release_requested = control.get(
            "charging_release_requested"
        )
        self.write_state.charging_setpoint = current
        self.write_state.current_write_generation = generation
        self.write_state.confirmed_current = values.get("charging_current_ems")
        self.write_state.energy_goal = goal
        self.write_state.soc_goal = soc_goal
        if self.on_update is not None:
            self.on_update()
        if require_available and payload.get("available") is not True:
            raise ServiceConnectionError("service_not_ready", "wallbox is unavailable at the service")
        energy_read = observed_at is not None and observed_at != self._last_energy_observed_at
        if consume_energy:
            self._last_energy_observed_at = observed_at
        if observed_at is not None:
            # Meter value and timestamp form one observation. Never attach a
            # sample timestamp to an independently carried-forward raw value.
            values = {**values, "energy_total": observed_energy}
        return WallboxData(
            values=values,
            failed_blocks=tuple(failed),
            current_write_generation=generation,
            service_read_only=control["read_only"],
            service_supports_cancel_pending=control.get("supports_cancel_pending", False),
            service_max_charging_current=control["max_charging_current"],
            service_charging_setpoint=current,
            pending_change=pending_action,
            pending_change_until=pending_until,
            connection_quality=quality,
            energy_goal=goal.snapshot() if goal_supported else None,
            soc_goal=soc_goal.snapshot() if soc_supported else None,
            charging_session=completed_session.as_dict() if completed_session is not None else None,
            session_summary_supported=session_supported,
            energy_read=energy_read,
            energy_observed_at=observed_at,
        )

    async def execute(self, command: str, **values: Any) -> None:
        # Deliberately no retries: a timed-out command may have reached the box.
        try:
            # A delayed poll response must not overwrite the command's newer
            # intent/readback. Serialize request and publication together.
            async with self._snapshot_lock:
                # Permission may have changed while this command waited for a
                # poll. In particular, a mismatched identity must block an
                # already queued command before it reaches the service.
                cancelling = command == "cancel_pending"
                metadata = command in {"soc_source", "soc_observation"}
                if not self._connected or (
                    not cancelling and not metadata and self.snapshot.get("available") is not True
                ):
                    raise ServiceConnectionError("service_not_ready", "wallbox is unavailable at the service")
                if cancelling and self.snapshot.get("control", {}).get("supports_cancel_pending") is not True:
                    raise DeviceValidationError("cancel_pending_not_supported")
                if cancelling and self.snapshot.get("control", {}).get("read_only") is not False:
                    raise DeviceValidationError("read_only_mode")
                if metadata and self.snapshot.get("control", {}).get("supports_soc_goal") is not True:
                    raise DeviceValidationError("soc_goal_not_supported")
                self.apply_snapshot(
                    await self.request(
                        "POST", "/v2/command", {
                            "command": command, **values,
                            "expected_serial_number": self._bound_serial or self.identity.serial_number,
                        }
                    ),
                    require_available=not (cancelling or metadata),
                    consume_energy=False,
                )
        except AmtronConnectionError as err:
            if isinstance(err, ServiceConnectionError) and err.reason == "service_auth_failed":
                raise DeviceError("service_auth_failed") from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_identity_changed":
                raise DeviceError("device_identity_changed") from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_version_mismatch":
                raise DeviceError("service_version_mismatch") from err
            raise DeviceError("service_unavailable") from err

    async def async_read_register(
        self, spec: RegisterSpec, *, verify_identity: bool = False,
    ) -> RegisterValue:
        if spec == ERROR_CODE:
            data = await self.request("POST", "/v2/read", {"register": spec.key})
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
        response = await self.request("POST", "/v2/read", {"block": block.name})
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
            data = await self._client.update()
        except AmtronConnectionError as err:
            if self.observe_session is not None:
                await self.observe_session(None)
            if isinstance(err, ServiceConnectionError) and err.reason == "service_auth_failed":
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="service_auth_failed"
                ) from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_identity_changed":
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="device_identity_changed"
                ) from err
            if isinstance(err, ServiceConnectionError) and err.reason == "service_version_mismatch":
                raise UpdateFailed(
                    translation_domain=DOMAIN, translation_key="service_version_mismatch"
                ) from err
            raise UpdateFailed(str(err)) from err
        if self.observe_session is not None:
            await self.observe_session(data)
        return data


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
        notify_pending: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            client,
            diagnostics=diagnostics,
            identity=client.identity,
            options=options,
            state=state,
            request_refresh=request_refresh,
            notify_pending=notify_pending,
        )
        self._remote = client

    @property
    def events(self) -> CommandEvents:
        """Expose only the bus owner's outcomes, never guessed HTTP results."""
        return self._remote.events

    @property
    def supports_energy_goal(self) -> bool:
        return self._remote.snapshot.get("control", {}).get("supports_energy_goal") is True

    @ha_command
    async def async_set_energy_goal(self, value: float) -> None:
        if not self.supports_energy_goal:
            raise DeviceValidationError("energy_goal_not_supported")
        candidate = EnergyGoal()
        try:
            candidate.set_target(value, 0)
        except ValueError as err:
            raise DeviceValidationError("energy_goal_invalid") from err
        await self._command("energy_goal", value=value)

    @property
    def supports_soc_goal(self) -> bool:
        return self._remote.snapshot.get("control", {}).get("supports_soc_goal") is True

    async def _soc_command(self, command: str, **values: Any) -> None:
        if not self.supports_soc_goal:
            raise DeviceValidationError("soc_goal_not_supported")
        if command == "soc_goal":
            self._require_write_access()
        await self._remote.execute(command, **values)
        # The command returns the authoritative cached snapshot. External car
        # telemetry must not cause an additional wallbox read or HTTP poll.
        if self._notify_pending is not None:
            self._notify_pending()

    @ha_command
    async def async_configure_soc(self, source: str | None) -> None:
        if source == self.soc_goal.source:
            return
        await self._soc_command("soc_source", source=source)

    @ha_command
    async def async_set_soc_goal(
        self, value: float, *, source: str, soc: float | None = None,
        maximum: float | None = None,
    ) -> None:
        await self._soc_command("soc_goal", value=value, source=source, soc=soc, maximum=maximum)

    @ha_command
    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
    ) -> None:
        await self._soc_command("soc_observation", source=source, soc=soc, maximum=maximum)

    @property
    def pending_action(self) -> str | None:
        """The bus-owning service retains queued work across HA restarts."""
        value = self._remote.snapshot.get("control", {}).get("pending_action")
        return str(value) if value is not None else None

    @property
    def pending_until(self) -> datetime | None:
        return self._remote.pending_until

    @property
    def supports_cancel_pending(self) -> bool:
        return self._remote.snapshot.get("control", {}).get("supports_cancel_pending") is True

    @property
    def can_cancel_pending(self) -> bool:
        return (
            self.supports_cancel_pending and self._remote.connected
            and self._remote.snapshot.get("control", {}).get("read_only") is False
            and self.pending_action is not None
        )

    @ha_command
    async def async_cancel_pending_change(self) -> None:
        """Cancel only service-owned future work, even while its wallbox is down."""
        previous = (self.pending_action, self.pending_until)
        await self._remote.execute("cancel_pending")
        if previous != (self.pending_action, self.pending_until) and self._notify_pending is not None:
            self._notify_pending()

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
