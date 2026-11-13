"""Own the Modbus connection and control lifetime independently of API clients."""

from __future__ import annotations

import asyncio
import logging
import math
import secrets
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from time import time as wall_clock
from typing import Any

from .charging_session import SessionHistory
from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .control import AmtronControl, ControlOptions
from .data import ConnectionState, DeviceIdentity, WallboxData, WriteDiagnostics
from .energy_goal import EnergyGoal
from .energy_goal_controller import EnergyGoalController
from .enums import RequestedPhases, SolarChargingMode
from .errors import DeviceError, DeviceValidationError
from .heartbeat import HeartbeatTask
from .identity import async_read_identity
from .persistence import StorageCommitUncertainError, StorageStateUnknownError
from .poller import DevicePoller
from .register_blocks import REGISTER_BLOCKS
from .registers import ENERGY_TOTAL, ERROR_CODE, REGISTERS_BY_KEY
from .service_config import ServerConfig, integer, number
from .service_store import IntentStore
from .service_version import SERVICE_VERSION
from .soc_goal import SocGoal
from .soc_goal_controller import SocGoalController
from .soc_transactions import SocTransactions
from .write_state import WriteState

_LOGGER = logging.getLogger(__name__)
API_VERSION = 2


class ModbusService:
    """All stateful operations remain here when HA disconnects or restarts."""

    def __init__(
        self,
        config: ServerConfig,
        state_path: Path,
        *,
        client: MennekesModbusClient | None = None,
    ) -> None:
        self.config = config
        self._connection_name = {
            "serial": "serial",
            "rtu_over_tcp": "RTU-over-TCP",
            "modbus_tcp": "Modbus TCP",
        }[config.connection.connection_type]
        self.client = client or MennekesModbusClient(
            config.connection, write_state=WriteState.cold_start(time.monotonic())
        )
        self.state_path = state_path
        self.identity = DeviceIdentity()
        self.data = WallboxData()
        self.diagnostics = WriteDiagnostics()
        self.connection = ConnectionState(available=False)
        self.heartbeat = HeartbeatTask(self.client, self.diagnostics)
        self.control: AmtronControl | None = None
        self.goal: EnergyGoalController | None = None
        self.soc_goal: SocGoalController | None = None
        self._poller: DevicePoller | None = None
        self._store: IntentStore | None = None
        self._poll_lock = asyncio.Lock()
        self._store_lock = asyncio.Lock()
        self._saved_session_history = self.client.write_state.session_history.snapshot()
        self._session_history_dirty = False
        self._session_history_retry_at = 0.0
        self._session_history_error_logged = False
        self._logged_unavailable = False
        self._command_lock = asyncio.Lock()
        # A fresh boot changes the revision, so a client cannot mistake an old
        # cached command outcome for a goal restored after a service restart.
        self._soc_goal_revision = secrets.token_hex(16)
        self.soc_transactions = SocTransactions(self)
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_started = asyncio.Event()
        self._success = False
        self._attempts = 0
        self._retry_at = 0.0
        self._cap_retry_at = 0.0
        self._updated_at: float | None = None
        self._energy_sample: dict[str, float | None] | None = None
        self._stopping = False
        self._phase = f"opening {self._connection_name} connection"
        self.heartbeat.async_add_listener(self._log_heartbeat_health)

    def start(self) -> asyncio.Task[None]:
        _LOGGER.info("Opening %s connection to the wallbox", self._connection_name)
        self._task = asyncio.create_task(self._run(), name="amtron_service")
        return self._task

    def _log_heartbeat_health(self) -> None:
        if self._stopping:
            return
        if self.heartbeat.healthy is True:
            _LOGGER.info("Heartbeat acknowledged by the wallbox")
        elif self.heartbeat.healthy is False:
            _LOGGER.warning(
                "Heartbeat acknowledgement missing; checking the %s connection",
                self._connection_name,
            )

    async def _initialize(self) -> None:
        self._phase = f"opening {self._connection_name} connection"
        await self.client.async_connect()
        self._phase = "reading wallbox identity"
        identity = await async_read_identity(self.client)
        store = IntentStore(
            self.state_path,
            identity.serial_number
            or f"{self.config.connection.port}:{self.config.connection.device_id}",
        )
        self._phase = "loading saved control intent"
        await asyncio.to_thread(store.load, self.client.write_state)
        self._saved_session_history = self.client.write_state.session_history.snapshot()
        self.identity = identity
        self._store = store
        self._poller = DevicePoller(
            self.client,
            identity,
            connection_state=self.connection,
            measurement_interval=self.config.measurement_interval,
            timeline=self.diagnostics.timeline,
        )
        control = AmtronControl(
            self.client,
            diagnostics=self.diagnostics,
            identity=identity,
            options=ControlOptions(
                self.config.read_only,
                self.config.current_cap,
                state_change_interval_seconds=self.config.state_change_interval_seconds,
                phase_switch_grace_seconds=self.config.phase_switch_grace_seconds,
            ),
            state=lambda: self.data,
            request_refresh=self._control_updated,
            validate_deferred=self._validate_deferred,
        )
        self._configure_control(control)
        goal = EnergyGoalController(control, self.client, self._persist)
        soc_goal = SocGoalController(control, self._persist)
        try:
            await control.async_sync_plug_charge()
            await self.refresh()
            control.observe_plug_charge(self.data)
            await control.async_sync_plug_charge()
        except BaseException:
            await control.async_shutdown()
            raise
        # With no saved resume limit, a migrated pause must not default to the
        # maximum available current. Only an explicit command may resume it.
        if control.charging_setpoint is None:
            current = self.data.get("charging_current_ems")
            if isinstance(current, (int, float)):
                control.charging_setpoint = (
                    float(current) if current == 0 or current >= 6 else 6.0
                )
        await self._persist()
        self.control = control
        self.goal = goal
        self.soc_goal = soc_goal
        goal.observe(self.data)
        soc_goal.retry_stop()
        _LOGGER.info(
            "%s connection established; wallbox initialized",
            self._connection_name[0].upper() + self._connection_name[1:],
        )
        if not self.config.read_only:
            self._heartbeat_task = self.heartbeat.start()
            self._heartbeat_started.set()
            _LOGGER.info("Heartbeat started")
        else:
            _LOGGER.info("Heartbeat disabled in read-only mode")

    def _configure_control(self, control: AmtronControl) -> None:
        """Allow an in-process service owner to install guards before first use."""

    async def _run(self) -> None:
        """Fail the service if its independent heartbeat writer exits."""
        if self.config.read_only:
            await self._polling_loop()
            return
        polling = asyncio.create_task(self._polling_loop(), name="amtron_polling")
        ready = asyncio.create_task(self._heartbeat_started.wait())
        try:
            done, _pending = await asyncio.wait(
                (polling, ready), return_when=asyncio.FIRST_COMPLETED
            )
            if polling in done:
                await polling
                return
            heartbeat = self._heartbeat_task
            if heartbeat is None:
                raise RuntimeError("Heartbeat task was not started")
            done, _pending = await asyncio.wait(
                (polling, heartbeat), return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                self._success = False
                reason = "cancelled" if heartbeat.cancelled() else "normal return"
                if not heartbeat.cancelled() and (error := heartbeat.exception()) is not None:
                    reason = type(error).__name__
                _LOGGER.error("Heartbeat task stopped unexpectedly (%s)", reason)
                raise RuntimeError("Heartbeat task stopped unexpectedly")
            await polling
        finally:
            # The owner stops the heartbeat separately, after the polling task
            # has finished any shielded state save. Never orphan a poller.
            for task in (polling, ready):
                task.cancel()
            await asyncio.gather(polling, ready, return_exceptions=True)

    async def _polling_loop(self) -> None:
        while True:
            try:
                if self.control is None:
                    await self._initialize()
                else:
                    await self.refresh()
                await self._recover()
                await self._enforce_current_cap()
                await self._persist()
                if self._logged_unavailable:
                    _LOGGER.info("Wallbox communication restored")
                self._logged_unavailable = False
            except (AmtronBusError, DeviceError, OSError, ValueError) as err:
                self._success = False
                if self.control is not None:
                    self.control.observe_plug_charge(None)
                if not self._logged_unavailable:
                    _LOGGER.warning(
                        "Wallbox unavailable during %s (%s); retrying every %.0f seconds",
                        self._phase,
                        type(err).__name__,
                        self.config.scan_interval,
                    )
                    self._logged_unavailable = True
            await asyncio.sleep(self.config.scan_interval)

    async def refresh(self) -> None:
        async with self._poll_lock:
            self._phase = "polling the wallbox"
            if self._poller is None:
                raise DeviceError("service_unavailable")
            goal_generation = self.client.write_state.energy_goal_generation
            observed_at = datetime.now(UTC)
            try:
                self.data = await self._poller.async_poll(
                    self.data, last_success=self._success
                )
            except AmtronBusError:
                self._success = False
                if self.control is not None:
                    self.control.observe_plug_charge(None)
                self._energy_sample = None
                self.data.energy_observed_at = None
                if self.identity.layout_version >= ENERGY_TOTAL.min_layout:
                    self.client.write_state.session_history.observe(None, now=wall_clock())
                if self.goal is not None:
                    self.goal.observe(WallboxData(
                        energy_read=True, energy_goal_generation=goal_generation,
                    ))
                raise
            self._success = True
            self._updated_at = time.monotonic()
            if self.data.energy_read:
                energy = self.data.get(ENERGY_TOTAL.key)
                # Keep the timestamp of a failed meter attempt as well. A
                # cached value must never turn a measurement gap into energy.
                self._energy_sample = {
                    "observed_at": wall_clock(),
                    "value": (
                        float(energy) if isinstance(energy, (int, float)) and not isinstance(energy, bool)
                        and math.isfinite(energy) and energy >= 0 else None
                    ),
                }
            self.data.energy_observed_at = (
                self._energy_sample["observed_at"] if self._energy_sample is not None else None
            )
            if self.control is not None:
                self.control.observe_plug_charge(self.data, observed_at=observed_at)
                self.control.observe_recovery(
                    self.data.fresh("error_code"), self.data.current_write_generation,
                )
            if self.goal is not None:
                self.goal.observe(self.data)
            if self.soc_goal is not None:
                self.soc_goal.retry_stop()
            if self.identity.layout_version >= ENERGY_TOTAL.min_layout:
                history = self.client.write_state.session_history
                self._session_history_dirty |= history.observe(self.data, now=wall_clock())
                self.data.session_summary_supported = True
                self.data.charging_session = history.latest.as_dict() if history.latest else None
                await self._persist_session_history()

    async def _persist_session_history(self) -> None:
        """Save completed summaries without making optional metrics a bus failure."""
        if not self._session_history_dirty or time.monotonic() < self._session_history_retry_at:
            return
        try:
            await self._persist(include_session_history=True)
        except (OSError, ValueError) as err:
            self._session_history_retry_at = time.monotonic() + 60
            if not self._session_history_error_logged:
                _LOGGER.warning("Cannot save completed charging session (%s)", type(err).__name__)
                self._session_history_error_logged = True
        else:
            self._session_history_error_logged = False

    async def _recover(self) -> None:
        self._phase = "recovering the wallbox error"
        if self.data.get("error_code") == 0:
            self._attempts = 0
        if (
            self.control is None
            or self.config.read_only
            or self.heartbeat.healthy is not True
            or self.data.get("error_code") != 200
            or self._attempts >= 3
            or time.monotonic() < self._retry_at
        ):
            return
        try:
            async with self._command_lock:
                attempted = await self.control.async_recover_from_error()
            self._attempts += int(attempted)
            self._retry_at = time.monotonic() + 30
            await self.refresh()
        except DeviceValidationError as err:
            if err.translation_key == "write_rate_limited":
                self._retry_at = time.monotonic() + 5
            else:
                self._attempts += 1
                self._retry_at = time.monotonic() + 30
        except DeviceError, AmtronBusError:
            self._attempts += 1
            self._retry_at = time.monotonic() + 30
        finally:
            await self._persist()

    async def _enforce_current_cap(self) -> None:
        """Never leave a confirmed charging current above a lowered cap."""
        control = self.control
        if control is None or self.config.read_only or time.monotonic() < self._cap_retry_at:
            return
        self._phase = "applying the current cap"
        try:
            async with self._command_lock:
                lowered = await control.async_enforce_current_cap()
        except (DeviceError, AmtronBusError) as err:
            self._cap_retry_at = time.monotonic() + 30
            _LOGGER.warning(
                "Charging current above the %.1f A cap could not be lowered (%s); retrying in 30 seconds",
                control.max_charging_current,
                err.translation_key if isinstance(err, DeviceError) else type(err).__name__,
            )
            return
        if lowered:
            _LOGGER.info("Charging current lowered to the %.1f A cap", control.max_charging_current)

    async def _persist(self, *, include_session_history: bool = False) -> None:
        if self._store is not None:
            async with self._store_lock:
                self._phase = "saving control intent"
                original = self.client.write_state
                history = (
                    original.session_history.snapshot() if include_session_history
                    else self._saved_session_history
                )
                state = WriteState(
                    charging_setpoint=original.charging_setpoint,
                    pause_requested=original.pause_requested,
                    charging_release_requested=original.charging_release_requested,
                    energy_goal=EnergyGoal.restore(original.energy_goal.snapshot()),
                    soc_goal=SocGoal.restore(original.soc_goal.snapshot()),
                    session_history=SessionHistory.restore(history),
                    plug_charge=replace(original.plug_charge, session=None, authorized=False),
                )
                saving = asyncio.create_task(asyncio.to_thread(self._store.save, state))
                cancelled = False
                # The worker thread cannot be cancelled. Keep the store lock
                # until it completes, even if shutdown or the HTTP handler is
                # cancelled again; otherwise an older save can overwrite a
                # newer intent after that newer save has already returned.
                try:
                    while not saving.done():
                        try:
                            await asyncio.shield(saving)
                        except asyncio.CancelledError:
                            cancelled = True
                    saving.result()
                except StorageCommitUncertainError:
                    if include_session_history:
                        # An ordinary intent save must retain the summary now
                        # visible on disk, even though its fsync needs retry.
                        self._saved_session_history = history
                        self._session_history_dirty = True
                    raise
                if include_session_history:
                    self._saved_session_history = history
                    self._session_history_dirty = original.session_history.snapshot() != history
                if cancelled:
                    raise asyncio.CancelledError

    async def _control_updated(self) -> None:
        """Publish queue changes and persist commands executed by the worker."""
        # A command may already have reached the wallbox. Save its intent
        # before a poll can fail or be cancelled during a connection outage.
        await self._persist()
        await self.refresh()

    def _validate_deferred(self) -> None:
        """A queued command needs the same fresh connection as an API request."""
        if self._stopping or not self._is_available():
            raise DeviceError("service_unavailable")

    def _is_available(self) -> bool:
        """Share the freshness boundary for state publication and command admission."""
        return (
            self._success
            and self.client.identity_verified
            and self._updated_at is not None
            and time.monotonic() - self._updated_at
            <= max(30, self.config.scan_interval * 3)
        )

    def snapshot(self) -> dict[str, Any]:
        control = self.control
        return {
            "api_version": API_VERSION,
            "service_version": SERVICE_VERSION,
            "identity": asdict(self.identity),
            "available": self._is_available(),
            "values": self.data.values,
            "failed_blocks": list(self.data.failed_blocks),
            "current_write_generation": self.data.current_write_generation,
            "connection_quality": self.data.connection_quality,
            "connection_failures": self.data.connection_failures,
            "connection_retries": self.data.connection_retries,
            "energy_sample": dict(self._energy_sample) if self._energy_sample is not None else None,
            "charging_session": {
                "supported": self.identity.layout_version >= ENERGY_TOTAL.min_layout,
                "latest": (
                    self.client.write_state.session_history.latest.as_dict()
                    if self.identity.layout_version >= ENERGY_TOTAL.min_layout
                    and self.client.write_state.session_history.latest is not None
                    else None
                ),
            },
            "command_events": control.events.snapshot() if control else None,
            "control": {
                "supports_plug_charge": True,
                "plug_charge": self.client.write_state.plug_charge.snapshot(),
                # A separate key: older integrations reject unknown gate fields.
                "plug_charge_manual": self.client.write_state.plug_charge.manual,
                "supports_energy_goal": control.supports_energy_goal if control else False,
                "supports_soc_goal": control.supports_soc_goal if control else False,
                "soc_transactions_supported": True,
                "soc_goal_revision": self._soc_goal_revision,
                "soc_goal": control.soc_goal.snapshot() if control else None,
                "energy_goal": control.energy_goal.snapshot() if control else None,
                "read_only": self.config.read_only,
                "supports_cancel_pending": True,
                "current_cap": self.config.current_cap,
                "state_change_interval_seconds": self.config.state_change_interval_seconds,
                "phase_switch_grace_seconds": self.config.phase_switch_grace_seconds,
                "pending_action": control.pending_action if control else None,
                "pending_until": (
                    control.pending_until.isoformat()
                    if control and control.pending_until is not None else None
                ),
                "max_charging_current": control.max_charging_current if control else 6,
                "charging_setpoint": control.charging_setpoint if control else None,
                "pause_requested": control.pause_requested if control else None,
                "charging_release_requested": control.charging_release_requested
                if control
                else None,
            },
            "heartbeat": {
                "running": self.heartbeat.running,
                "healthy": self.heartbeat.healthy,
            },
            "diagnostics": {
                key: getattr(self.diagnostics, key)
                for key in (
                    "heartbeats_sent",
                    "heartbeats_failed",
                    "heartbeats_late",
                    "writes_sent",
                    "writes_rejected",
                    "writes_rate_limited",
                    "recoveries",
                )
            },
            "write_errors": sorted(self.diagnostics.write_errors),
        }

    async def stable_snapshot(self) -> dict[str, Any]:
        """Publish a goal only after its enclosing command has completed."""
        async with self._command_lock:
            return self.snapshot()

    async def command(
        self, payload: dict[str, Any], *,
        _soc_outcome: Callable[[str, str, BaseException | None], None] | None = None,
    ) -> dict[str, Any]:
        if payload.keys() - {
            "command", "value", "allow_unlimited", "current_cap", "expected_serial_number",
            "source", "soc", "maximum", "deadline", "owner", "expected_soc_revision",
            "session", "policy",
        }:
            raise ValueError("unknown command field")
        if "expected_serial_number" not in payload:
            raise ValueError("expected_serial_number is required")
        expected_serial = payload["expected_serial_number"]
        if expected_serial is not None and (
            not isinstance(expected_serial, str) or not 1 <= len(expected_serial) <= 256
        ):
            raise ValueError("expected_serial_number must be a non-empty string or null")
        async with self._command_lock:
            control = self.control
            # A request can age while another command owns the lock. Only admit
            # it against the same current state that the API reports as usable.
            command = payload.get("command")
            cancelling = command in ("cancel_pending", "cancel_timed_current")
            metadata = command in ("soc_source", "soc_observation") or (
                command == "plug_charge_config" and payload.get("value") is False
            )
            if control is None or self._stopping or (not cancelling and not metadata and not self._is_available()):
                raise DeviceError("service_unavailable")
            # The service can restart against another wallbox between HA polls,
            # or while this command waits. Bind the write before any bus access.
            # Explicit null matches only a device without a readable serial.
            if expected_serial != self.identity.serial_number:
                raise DeviceError("device_identity_changed")
            if "expected_soc_revision" in payload and (
                command != "soc_observation"
                or payload["expected_soc_revision"] != self._soc_goal_revision
            ):
                raise DeviceError("soc_goal_source_changed")
            initial_revision = self._soc_goal_revision
            value = payload.get("value")
            try:
                owner = payload.get("owner")
                if command == "plug_charge_config":
                    enabled = payload.get("value")
                    policy = payload.get("policy", "")
                    if type(enabled) is not bool or not isinstance(policy, str):
                        raise ValueError("invalid Plug & Charge configuration")
                    await control.async_configure_plug_charge(enabled, policy=policy)
                elif command == "plug_charge_authorize":
                    session = payload.get("session")
                    deadline = payload.get("deadline")
                    if not isinstance(session, str) or not isinstance(deadline, str) or len(deadline) > 64:
                        raise ValueError("invalid Plug & Charge authorization")
                    await control.async_authorize_plug_charge(session, deadline=datetime.fromisoformat(deadline))
                elif command == "cancel_timed_current":
                    if not isinstance(owner, str):
                        raise ValueError("invalid timed command owner")
                    await control.async_cancel_timed_current(owner)
                elif cancelling:
                    await control.async_cancel_pending_change()
                elif command == "timed_current":
                    if not isinstance(owner, str):
                        raise ValueError("invalid timed command owner")
                    deadline = payload.get("deadline")
                    if not isinstance(deadline, str) or len(deadline) > 64:
                        raise ValueError("invalid timed command deadline")
                    await control.async_set_timed_current(
                        None if value is None else number(value, 6, 32),
                        deadline=datetime.fromisoformat(deadline), owner=owner,
                    )
                elif command == "set_current":
                    unlimited = payload.get("allow_unlimited", False)
                    if not isinstance(unlimited, bool):
                        raise ValueError("allow_unlimited must be boolean")
                    await control.async_set_charging_current(
                        number(value, 0, 32), allow_unlimited=unlimited
                    )
                elif command == "pause":
                    await control.async_pause_charging()
                elif command == "energy_goal":
                    await control.async_set_energy_goal(number(value, 0, 1000))
                elif command == "soc_source":
                    source = payload.get("source")
                    if source is not None and not isinstance(source, str):
                        raise ValueError("source must be a string or null")
                    before = control.soc_goal.snapshot()
                    success = False
                    try:
                        await control.async_configure_soc(source)
                        success = True
                    finally:
                        if success or control.soc_goal.snapshot() != before:
                            self._soc_goal_revision = secrets.token_hex(16)
                elif command in ("soc_goal", "soc_observation"):
                    source = payload.get("source")
                    if not isinstance(source, str):
                        raise ValueError("source is required")
                    soc = payload.get("soc")
                    maximum = payload.get("maximum")
                    if command == "soc_goal":
                        before = control.soc_goal.snapshot()
                        success = False
                        try:
                            await control.async_set_soc_goal(
                                number(value, 0, 100), source=source,
                                soc=None if soc is None else number(soc, 0, 100),
                                maximum=None if maximum is None else number(maximum, 0, 100),
                            )
                            success = True
                        finally:
                            if success or control.soc_goal.snapshot() != before:
                                self._soc_goal_revision = secrets.token_hex(16)
                    else:
                        await control.async_observe_soc(
                            None if soc is None else number(soc, 0, 100),
                            None if maximum is None else number(maximum, 0, 100), source=source,
                        )
                elif command == "resume":
                    await control.async_resume_charging(
                        current_cap=number(
                            payload.get("current_cap", self.config.current_cap), 6, 32
                        )
                    )
                elif command in ("release", "lock"):
                    if not isinstance(value, bool):
                        raise ValueError("value must be boolean")
                    if command == "release":
                        await control.async_set_charging_release(value)
                    else:
                        await control.async_set_lock_evse(value)
                elif command == "solar":
                    await control.async_set_solar_charging_mode(
                        SolarChargingMode(integer(value, 0, 3))
                    )
                elif command == "phases":
                    await control.async_set_requested_phases(
                        RequestedPhases(integer(value, 0, 1))
                    )
                elif command == "recover":
                    await control.async_recover_from_error()
                elif command == "restart":
                    await control.async_restart()
                else:
                    raise ValueError("unknown command")
            finally:
                failure = sys.exception()
                try:
                    await self._persist()
                except BaseException as err:
                    if not isinstance(failure, (StorageCommitUncertainError, StorageStateUnknownError)):
                        failure = err
                    raise
                finally:
                    if _soc_outcome is not None:
                        _soc_outcome(initial_revision, self._soc_goal_revision, failure)
            if not cancelling and not metadata:
                await self.refresh()
            return self.snapshot()

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        # An allowlist of known reads, never a raw Modbus write tunnel.
        if set(payload) == {"register"}:
            key = payload["register"]
            if key != ERROR_CODE.key:
                raise ValueError("unsupported live register read")
            return {
                "value": await self.client.async_read_register(REGISTERS_BY_KEY[key])
            }
        if set(payload) == {"block"}:
            block = next(
                (
                    block
                    for block in REGISTER_BLOCKS
                    if block.name == payload["block"]
                    and "serial_number" not in block.keys
                ),
                None,
            )
            if block is None:
                raise ValueError("unsupported diagnostic block")
            return {
                "words": await self.client.async_read_words(
                    address=block.address, count=block.count
                )
            }
        raise ValueError("unsupported read")

    async def stop(self) -> None:
        self._stopping = True
        try:
            await self.soc_transactions.close()
            try:
                if self._task is not None:
                    self._task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._task
            finally:
                heartbeat_running = self.heartbeat.running
                await self.heartbeat.async_stop()
                if heartbeat_running:
                    _LOGGER.info("Heartbeat stopped")
                if self.control is not None:
                    if self.goal is not None:
                        await self.goal.async_stop()
                    if self.soc_goal is not None:
                        await self.soc_goal.async_stop()
                    await self.control.async_shutdown()
                self._session_history_retry_at = 0.0
                await self._persist_session_history()
                await self._persist()
        finally:
            await self.client.async_close()
            _LOGGER.info(
                "%s connection closed",
                self._connection_name[0].upper() + self._connection_name[1:],
            )
