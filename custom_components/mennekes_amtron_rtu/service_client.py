"""Home Assistant client for the independent Modbus service.

Disconnecting this client never stops the server or writes a charging command.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession

from .device.charging_display import DISPLAY_OPTIONS
from .device.client_errors import AmtronConnectionError
from .device.command_events import CommandEvents, command_source
from .device.commands import needs_wallbox
from .device.const import (
    MAX_PHASE_SWITCH_GRACE_SECONDS,
    MAX_STATE_CHANGE_INTERVAL_SECONDS,
)
from .device.control import PENDING_ACTIONS
from .device.data import DeviceIdentity, WallboxData
from .device.decode import is_hex32
from .device.departure_plan import DepartureState
from .device.energy_goal import EnergyGoal
from .device.errors import DeviceError, DeviceValidationError
from .device.identity import usable_max_evse_current
from .device.link_quality import CONNECTION_QUALITY_STATES
from .device.meter_log import parse_charge_meter
from .device.operation import Operation
from .device.register_blocks import REGISTER_BLOCKS
from .device.registers import ERROR_CODE
from .device.soc_goal import SocGoal
from .service_compat import SUPPORTED_API_VERSION, validate_service_version
from .service_errors import ServiceConnectionError, ServiceFailure
from .service_transport import HttpTransport, ServiceTransport

CONF_SERVER_URL = "server_url"
CONF_SERVER_TOKEN = "server_token"  # noqa: S105 - configuration key, not a credential


def _pending_status(control: dict[str, Any]) -> tuple[str | None, datetime | None]:
    """Validate the current service queue contract."""
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


class ServiceClient:
    """The service's state and commands, however the service is reached."""

    def __init__(
        self, session: ClientSession | None = None, url: str = "", token: str = "",
        *, transport: ServiceTransport | None = None,
    ) -> None:
        if transport is None:
            if session is None:
                raise ValueError("a session or a transport is required")
            transport = HttpTransport(session, server_url(url), token)
        self._transport = transport
        self._connected = False
        self.snapshot: dict[str, Any] = {}
        self.identity = DeviceIdentity()
        self._bound_serial: str | None = None
        self.failure_reason: ServiceFailure | None = None
        self.on_update: Callable[[], None] | None = None
        self.on_auth_failed: Callable[[], None] | None = None
        self.on_auth_error: Callable[[bool], None] | None = None
        self._snapshot_lock = asyncio.Lock()
        # The service's departure plan from the last valid snapshot.
        self.departure: DepartureState | None = None
        # The service's summary of who controls the charge, None for a service without one.
        self.operation: Operation | None = None
        self.pending_until: datetime | None = None
        self.events = CommandEvents(supported=False)
        self.energy_goal = EnergyGoal()
        self.soc_goal = SocGoal()
        self._last_energy_observed_at: float | None = None
        # The newest meter value that was really read. An observation whose
        # read failed carries none.
        self.last_energy_total: float | None = None

    def bind_identity(self, serial_number: str | None) -> None:
        """Bind a configured client; setup probes remain free to read identity."""
        if self._bound_serial is not None and self._bound_serial != serial_number:
            raise ValueError("service identity is already bound")
        self._bound_serial = serial_number

    @property
    def bound_serial(self) -> str | None:
        """The serial number this entry is bound to, as commands name it."""
        return self._bound_serial or self.identity.serial_number

    @property
    def connected(self) -> bool:
        return self._connected

    async def async_connect(self) -> None:
        await self.update()

    async def async_close(self) -> None:
        self._connected = False
        self.on_auth_failed = None
        self.on_auth_error = None

    def _mark_unavailable(self, reason: ServiceFailure | None = None) -> None:
        """Publish a failed connection immediately, including command failures."""
        new_auth_failure = reason == "service_auth_failed" and self.failure_reason != reason
        self.failure_reason = reason
        self._connected = False
        if self.on_update is not None:
            self.on_update()
        if reason == "service_auth_failed" and self.on_auth_error is not None:
            self.on_auth_error(new_auth_failure)

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None,
        *, update_connection: bool = True,
    ) -> dict[str, Any]:
        try:
            status, data = await self._transport.call(method, path, payload)
            if status == 409:
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
            if status != 200:
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

    async def update(self, *, consume_energy: bool = True) -> WallboxData:
        """Fetch the state; ``consume_energy`` off leaves the meter sample to the next poll."""
        async with self._snapshot_lock:
            payload = await self.request("GET", "/v2/state")
            return self.apply_snapshot(payload, consume_energy=consume_energy)

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
            if "command_events" not in payload:
                raise ValueError("missing command events")
            command_events = CommandEvents.parse(payload["command_events"])
            section = "charging_display"
            display = payload.get("charging_display")
            if display is not None and display not in DISPLAY_OPTIONS:
                raise ValueError("invalid charging display")
            section = "connection_quality"
            quality = payload.get("connection_quality")
            if quality is not None and quality not in CONNECTION_QUALITY_STATES:
                raise ValueError("invalid connection quality")
            failures = payload["connection_failures"]
            if failures is not None and (type(failures) is not int or failures < 0):
                raise ValueError("invalid connection failure count")
            retried = payload["connection_retries"]
            if retried is not None and (type(retried) is not int or retried < 0):
                raise ValueError("invalid connection retry count")
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
            section = "charge_meter"
            charge_meter = parse_charge_meter(payload["charge_meter"])
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
            pending_until = _pending_status(control)[1]
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
            revision = control.get("soc_goal_revision")
            if not is_hex32(revision):
                raise ValueError("invalid percentage goal revision")
            section = "control.soc_maximum_required"
            maximum_required = control.get("soc_maximum_required")
            if maximum_required is not None and type(maximum_required) is not bool:
                raise ValueError("invalid percentage maximum policy")
            section = "control.departure"
            departure_raw = control.get("departure")
            departure = None if departure_raw is None else DepartureState.restore(departure_raw)
            section = "control.operation"
            operation_raw = control.get("operation")
            operation = None if operation_raw is None else Operation.restore(operation_raw)
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
            # This optional hardware capability can be unavailable.
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
            section = "control.plug_charge"
            gate = control.get("plug_charge")
            if (
                not isinstance(gate, dict)
                or gate.keys() != {"enabled", "policy", "session", "since", "authorized"}
                or type(gate["enabled"]) is not bool
                or type(gate["authorized"]) is not bool
                or not isinstance(gate["policy"], str)
                or (gate["policy"] != "" and (
                    len(gate["policy"]) != 64
                    or any(character not in "0123456789abcdef" for character in gate["policy"])
                ))
                or (gate["session"] is not None and (
                    not isinstance(gate["session"], str) or len(gate["session"]) != 32
                    or any(character not in "0123456789abcdef" for character in gate["session"])
                ))
                or (gate["authorized"] and (not gate["enabled"] or gate["session"] is None))
            ):
                raise ValueError("invalid plug charge state")
            since = gate["since"]
            if gate["session"] is None:
                if since is not None:
                    raise ValueError("unexpected plug charge start")
            elif (
                not isinstance(since, str) or len(since) > 64
                or (started := datetime.fromisoformat(since)).tzinfo is None
                or started.utcoffset() is None
            ):
                raise ValueError("invalid plug charge start")
            manual = control["plug_charge_manual"]
            if type(manual) is not bool or (manual and not gate["enabled"]):
                raise ValueError("invalid plug charge release")
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
        self.energy_goal = goal
        self.soc_goal = soc_goal
        self.departure = departure
        self.operation = operation
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
            if observed_energy is not None:
                self.last_energy_total = observed_energy
        return WallboxData(
            values=values,
            charging_display=display,
            failed_blocks=tuple(failed),
            current_write_generation=generation,
            pending_change_until=pending_until,
            # A copy: what entities compare never changes under them.
            service_control=deepcopy(control),
            connection_quality=quality,
            connection_failures=failures,
            connection_retries=retried,
            energy_goal=goal.snapshot() if goal_supported else None,
            soc_goal=soc_goal.snapshot() if soc_supported else None,
            energy_read=energy_read,
            energy_observed_at=observed_at,
            charge_meter=charge_meter,
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
                # The service's own table says which commands need the wallbox.
                wallbox = needs_wallbox(command, values)
                soc_metadata = command in {"soc_source", "soc_observation"}
                if not self._connected or (wallbox and self.snapshot.get("available") is not True):
                    raise ServiceConnectionError("service_not_ready", "wallbox is unavailable at the service")
                if command == "cancel_pending" and self.snapshot.get("control", {}).get("read_only") is not False:
                    raise DeviceValidationError("read_only_mode")
                if soc_metadata and self.snapshot.get("control", {}).get("supports_soc_goal") is not True:
                    raise DeviceValidationError("soc_goal_not_supported")
                self.apply_snapshot(
                    await self.request(
                        "POST", "/v2/command", {
                            "command": command, **values, "origin": command_source.get(),
                            "expected_serial_number": self.bound_serial,
                        }
                    ),
                    require_available=wallbox,
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

    async def async_energy_between(self, start: float, end: float) -> float | None:
        """What the meter delivered in a time span, from the service's log."""
        # An estimate must not mark the entry unavailable.
        data = await self.request(
            "POST", "/v2/energy", {"start": start, "end": end}, update_connection=False,
        )
        value = data.get("energy_kwh")
        if value is None:
            return None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise AmtronConnectionError("invalid energy span")
        return float(value)

    async def async_read_error_code(self) -> int:
        """Read the wallbox's error register now, not from the last poll."""
        data = await self.request("POST", "/v2/read", {"register": ERROR_CODE.key})
        value = data.get("value")
        if type(value) is not int:
            raise AmtronConnectionError("invalid error register")
        return value

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
        # Diagnostics reads must not mark the entry unavailable.
        response = await self.request("POST", "/v2/read", {"block": block.name}, update_connection=False)
        words = response.get("words")
        if (
            not isinstance(words, list)
            or len(words) != count
            or not all(type(word) is int and 0 <= word <= 65535 for word in words)
        ):
            raise AmtronConnectionError("invalid register image")
        return words
