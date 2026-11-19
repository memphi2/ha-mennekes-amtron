"""What Home Assistant may ask of the service, and what the service reports.

Every value here is the service's own: this class reads its last state and
sends commands. It decides nothing about the wallbox.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Coroutine
from copy import copy
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from .data import WallboxData
from .device.client_errors import AmtronConnectionError
from .device.command_events import CommandEvents, command_source
from .device.const import CHARGING_CURRENT_MINIMUM
from .device.control_types import pause_state
from .device.decode import is_hex32
from .device.departure_plan import DepartureInputs, DepartureState
from .device.energy_goal import EnergyGoal
from .device.enums import RequestedPhases, SolarChargingMode
from .device.errors import DeviceError, DeviceValidationError
from .device.operation import Operation
from .device.registers import CHARGING_CURRENT_EMS
from .device.soc_goal import SocGoal
from .exceptions import home_assistant_error, service_validation_error
from .service_client import ServiceClient


def ha_command[**P, T](
    method: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Preserve signatures while adapting errors at the HA boundary."""

    @wraps(method)
    async def call(*args: P.args, **kwargs: P.kwargs) -> T:
        token = command_source.set(command_source.get() if command_source.get() != "unknown" else "home_assistant")
        try:
            return await method(*args, **kwargs)
        except DeviceError as err:
            factory = (
                service_validation_error
                if isinstance(err, DeviceValidationError)
                else home_assistant_error
            )
            raise factory(
                err.translation_key, err.translation_placeholders
            ) from err.__cause__
        finally:
            command_source.reset(token)

    return call


_LOGGER = logging.getLogger(__name__)


class ServiceHeartbeat:
    """The service's heartbeat health, published on change only."""

    def __init__(self, client: ServiceClient) -> None:
        self._client = client
        self._healthy: bool | None = None
        self._listeners: set[Callable[[], None]] = set()
        self.update()

    @property
    def healthy(self) -> bool | None:
        return self._healthy

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def update(self) -> None:
        value = self._client.snapshot.get("heartbeat", {}).get("healthy")
        healthy = value if self._client.connected and isinstance(value, bool) else None
        if healthy is self._healthy:
            return
        self._healthy = healthy
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                # Whoever shows the heartbeat never fails the poll or the
                # command that brought the news.
                _LOGGER.exception("Heartbeat: listener failed")


class ServiceControl:
    """Commands are validated by the service that owns current and pause state."""

    def __init__(
        self,
        client: ServiceClient,
        *,
        state: Callable[[], WallboxData],
        request_refresh: Callable[[], Awaitable[None]],
        notify_pending: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._state = state
        self._request_refresh = request_refresh
        self._notify_pending = notify_pending
        self._soc_transaction: tuple[str, str | None] | None = None
        self._soc_transaction_fenced = False

    @property
    def _control(self) -> dict[str, Any]:
        control = self._client.snapshot.get("control")
        return control if isinstance(control, dict) else {}

    def guest_active(self) -> bool:
        return self._control.get("guest_mode") is True

    @property
    def energy_goal(self) -> EnergyGoal:
        return self._client.energy_goal

    @property
    def soc_goal(self) -> SocGoal:
        return self._client.soc_goal

    @property
    def energy_goal_stop_requested(self) -> bool:
        """A reached energy or vehicle target that fences further starts."""
        return self.energy_goal.stop_requested or (
            not self.guest_active() and self.soc_goal.stop_requested
        )

    @property
    def is_paused(self) -> bool | None:
        """Whether the last confirmed current is a pause; None while unknown."""
        return pause_state(self._client.snapshot.get("values", {}).get(CHARGING_CURRENT_EMS.key))

    @property
    def observed_pause(self) -> bool | None:
        """Fresh current evidence for automatic starts, never a held value."""
        data = self._state()
        if (
            # A command answered since: this reading is older than its writes.
            data.current_write_generation != self._client.snapshot.get("current_write_generation")
            or "current_limits" in data.failed_blocks
        ):
            return None
        return pause_state(data.fresh(CHARGING_CURRENT_EMS.key))

    @property
    def soc_goal_revision(self) -> str | None:
        """Identify the service goal used when folding vehicle events."""
        revision = self._control.get("soc_goal_revision")
        return revision if isinstance(revision, str) else None

    @property
    def soc_goal_resolution_pending(self) -> bool:
        return self._soc_transaction is not None

    @property
    def soc_goal_resolution_fenced(self) -> bool:
        return self._soc_transaction is not None and self._soc_transaction_fenced

    def abandon_fenced_soc_goal(self) -> None:
        """Let a user's later explicit target supersede an unresolved request."""
        if not self.soc_goal_resolution_fenced:
            raise DeviceError("service_unavailable")
        self._soc_transaction = None
        self._soc_transaction_fenced = False

    async def _soc_transaction_request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._client.request("POST", path, payload)
        except AmtronConnectionError as err:
            raise DeviceError("service_unavailable") from err

    def _apply_soc_transaction_result(self, result: dict[str, Any], previous_revision: str | None) -> str:
        outcome, snapshot, revision = result.get("outcome"), result.get("snapshot"), result.get("revision")
        if (
            not isinstance(outcome, str)
            or outcome not in ("applied", "rejected", "cancelled", "unknown")
            or not isinstance(snapshot, dict)
        ):
            raise DeviceError("service_unavailable")
        try:
            self._client.apply_snapshot(snapshot, require_available=False, consume_energy=False)
        except AmtronConnectionError as err:
            # A reply that is no valid state: reported like a lost service.
            raise DeviceError("service_unavailable") from err
        if outcome == "unknown":
            return outcome
        current = self.soc_goal_revision
        expected = revision if outcome == "applied" else previous_revision
        if not isinstance(expected, str) or current != expected:
            outcome = "superseded"
        self._soc_transaction = None
        self._soc_transaction_fenced = False
        return outcome

    @ha_command
    async def async_resolve_soc_goal(self) -> str:
        """Fence a possibly accepted request before choosing its event fold."""
        if self._soc_transaction is None:
            return "superseded"
        token, previous_revision = self._soc_transaction
        async with self._client._snapshot_lock:  # noqa: SLF001 - shared publication fence
            result = await self._soc_transaction_request("/v2/soc-goal/resolve", {"token": token})
            outcome = self._apply_soc_transaction_result(result, previous_revision)
            if outcome == "unknown":
                self._soc_transaction_fenced = True
            return outcome

    @property
    def events(self) -> CommandEvents:
        """Expose only the bus owner's outcomes, never guessed HTTP results."""
        return self._client.events

    @property
    def supports_energy_goal(self) -> bool:
        return self._control.get("supports_energy_goal") is True

    @property
    def plug_charge(self) -> dict[str, Any]:
        control = self._control
        gate = control.get("plug_charge")
        if not isinstance(gate, dict):
            return {"enabled": False, "policy": "", "session": None, "since": None, "authorized": False, "manual": False}
        return {**gate, "manual": control.get("plug_charge_manual", False)}

    @ha_command
    async def async_configure_plug_charge(self, enabled: bool, *, policy: str = "") -> None:
        if enabled is not False:
            await self._command("plug_charge_config", value=enabled, policy=policy)
            return
        # Disabling only removes this optional policy. The bus owner keeps its
        # release hold, so this is allowed even after switching to read-only.
        await self._client.execute("plug_charge_config", value=False, policy=policy)
        if self._notify_pending is not None:
            self._notify_pending()

    @ha_command
    async def async_authorize_plug_charge(self, session: str, *, deadline: datetime) -> None:
        await self._command(
            "plug_charge_authorize", session=session, deadline=deadline.isoformat(),
        )

    @ha_command
    async def async_set_energy_goal(self, value: float, *, expected: float | None = None) -> None:
        """Set the energy target; with ``expected``, only while it still is that."""
        if not self.supports_energy_goal:
            raise DeviceValidationError("energy_goal_not_supported")
        candidate = EnergyGoal()
        try:
            candidate.set_target(value, 0)
        except ValueError as err:
            raise DeviceValidationError("energy_goal_invalid") from err
        if expected is None:
            await self._command("energy_goal", value=value)
        else:
            await self._command("energy_goal", value=value, expected_energy_goal=expected)

    @property
    def supports_soc_goal(self) -> bool:
        return self._control.get("supports_soc_goal") is True

    async def _soc_command(self, command: str, **values: Any) -> None:
        if not self.supports_soc_goal:
            raise DeviceValidationError("soc_goal_not_supported")
        if command == "soc_goal":
            self._require_write_access()
        await self._client.execute(command, **values)
        # The command returns the authoritative cached snapshot. External car
        # telemetry must not cause an additional wallbox read or HTTP poll.
        if self._notify_pending is not None:
            self._notify_pending()

    @property
    def soc_configuration_required(self) -> bool:
        """A new service boot needs HA's optional-cap policy again."""
        return self._control.get("soc_maximum_required") is None

    @ha_command
    async def async_configure_soc(self, source: str | None, *, maximum_required: bool = False) -> None:
        known = self._control.get("soc_maximum_required")
        if source == self.soc_goal.source and known is maximum_required:
            return
        await self._soc_command("soc_source", source=source, maximum_required=maximum_required)

    @ha_command
    async def async_set_soc_goal(
        self, value: float, *, source: str, soc: float | None = None,
        maximum: float | None = None,
    ) -> None:
        if not self.supports_soc_goal:
            raise DeviceValidationError("soc_goal_not_supported")
        if self._soc_transaction is not None:
            raise DeviceError("service_unavailable")
        self._require_write_access()
        candidate = copy(self.soc_goal)
        try:
            candidate.set_target(value, soc, maximum)
        except ValueError as err:
            raise DeviceValidationError("soc_goal_invalid") from err
        async with self._client._snapshot_lock:  # noqa: SLF001 - shared publication fence
            previous_revision = self.soc_goal_revision
            reservation = await self._soc_transaction_request("/v2/soc-goal/reserve", {})
            token = reservation.get("token")
            if not is_hex32(token):
                raise DeviceError("service_unavailable")
            self._soc_transaction = (token, previous_revision)
            self._soc_transaction_fenced = False
            result = await self._soc_transaction_request("/v2/soc-goal/commit", {
                "token": token, "expected_serial_number": self._client.bound_serial,
                "value": value, "source": source, "soc": soc, "maximum": maximum,
            })
            outcome = self._apply_soc_transaction_result(result, previous_revision)
            if outcome in ("rejected", "superseded"):
                # The service answered: it refused the target, or another
                # command changed it first. That is not an unreachable service.
                raise DeviceValidationError("soc_goal_unavailable")
            if outcome != "applied":
                raise DeviceError("service_unavailable")
        if self._notify_pending is not None:
            self._notify_pending()

    @ha_command
    async def async_observe_soc(
        self, soc: float | None, maximum: float | None = None, *, source: str,
        expected_soc_revision: str | None = None,
    ) -> None:
        values: dict[str, Any] = {"source": source, "soc": soc, "maximum": maximum}
        if expected_soc_revision is not None:
            values["expected_soc_revision"] = expected_soc_revision
        await self._soc_command("soc_observation", **values)

    @property
    def departure(self) -> DepartureState | None:
        return self._client.departure

    @property
    def operation(self) -> Operation | None:
        return self._client.operation

    async def _departure_command(self, command: str, **values: Any) -> None:
        await self._client.execute(command, **values)
        # As with the charge level: the reply is the service's own state and
        # needs no further wallbox read.
        if self._notify_pending is not None:
            self._notify_pending()

    @ha_command
    async def async_set_target_time(self, deadline: datetime | None) -> None:
        # The service would answer a past time as a malformed request, which
        # would read as an unreachable service instead of a wrong value.
        if deadline is not None and (deadline.utcoffset() is None or deadline <= datetime.now(UTC)):
            raise DeviceValidationError("target_time_not_future")
        revision = self._control.get("target_time_revision")
        await self._departure_command(
            "departure_choice", deadline=None if deadline is None else deadline.isoformat(),
            revision=revision,
        )

    @ha_command
    async def async_set_departure_trip(
        self, deadline: datetime | None, source: str | None, *, withdraw: bool = False,
        expected_selection: dict[str, Any] | None = None,
    ) -> None:
        await self._departure_command(
            "departure_trip", deadline=None if deadline is None else deadline.isoformat(),
            source=source, withdraw=withdraw,
            **({"expected_selection": expected_selection} if expected_selection is not None else {}),
        )

    @ha_command
    async def async_set_departure_inputs(self, inputs: DepartureInputs) -> None:
        await self._departure_command("departure_inputs", value=inputs.snapshot())

    @property
    def pending_action(self) -> str | None:
        """The bus-owning service retains queued work across HA restarts."""
        value = self._control.get("pending_action")
        return str(value) if value is not None else None

    @property
    def pending_until(self) -> datetime | None:
        return self._client.pending_until

    @property
    def can_cancel_pending(self) -> bool:
        return (
            self._client.connected
            and self._control.get("read_only") is False
            and self.pending_action is not None
        )

    @ha_command
    async def async_cancel_pending_change(self) -> None:
        """Cancel only service-owned future work, even while its wallbox is down."""
        previous = (self.pending_action, self.pending_until)
        await self._client.execute("cancel_pending")
        if previous != (self.pending_action, self.pending_until) and self._notify_pending is not None:
            self._notify_pending()

    @property
    def read_only(self) -> bool:
        return (
            not self._client.connected
            or self._client.snapshot.get("available") is not True
            or self._control.get("read_only") is not False
        )

    def _require_write_access(self) -> None:
        """Use current service permission, never a former HA-only setting."""
        if not self._client.connected or self._client.snapshot.get("available") is not True:
            raise DeviceError("service_unavailable")
        if self.read_only:
            raise DeviceValidationError("read_only_mode")

    @property
    def max_charging_current(self) -> float:
        """The service's limit: its cap and what the wallbox allows."""
        return float(self._control.get("max_charging_current", CHARGING_CURRENT_MINIMUM))

    @property
    def charging_setpoint(self) -> float | None:
        value = self._control.get("charging_setpoint")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def pause_requested(self) -> bool | None:
        value = self._control.get("pause_requested")
        return value if isinstance(value, bool) else None

    @property
    def charging_release_requested(self) -> bool | None:
        value = self._control.get("charging_release_requested")
        return value if isinstance(value, bool) else None

    async def _command(self, command: str, **values: Any) -> None:
        self._require_write_access()
        await self._client.execute(command, **values)
        if self._request_refresh is not None:
            await self._request_refresh()

    @ha_command
    async def async_set_charging_current(
        self, value: float, *, allow_unlimited: bool = False
    ) -> None:
        # The service answers a negative current as a malformed request, which
        # would read as an unreachable service instead of a wrong value.
        if not math.isfinite(value) or value < 0:
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
        maximum = self.max_charging_current
        if current_cap is not None:
            if not math.isfinite(current_cap) or current_cap < CHARGING_CURRENT_MINIMUM:
                raise DeviceValidationError(
                    "charging_current_invalid",
                    {"value": str(current_cap), "minimum": str(CHARGING_CURRENT_MINIMUM)},
                )
            maximum = min(current_cap, maximum)
        await self._command("resume", current_cap=maximum)

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
