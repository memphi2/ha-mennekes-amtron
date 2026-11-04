"""Own the serial port and control lifetime independently of every API client."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .client import MennekesModbusClient
from .client_errors import AmtronBusError
from .control import AmtronControl, ControlOptions
from .data import ConnectionState, DeviceIdentity, WallboxData, WriteDiagnostics
from .enums import RequestedPhases, SolarChargingMode
from .errors import DeviceError, DeviceValidationError
from .heartbeat import HeartbeatTask
from .identity import async_read_identity
from .poller import DevicePoller
from .register_blocks import REGISTER_BLOCKS
from .registers import ERROR_CODE, REGISTERS_BY_KEY
from .service_config import ServerConfig, integer, number
from .service_store import IntentStore
from .write_state import WriteState

_LOGGER = logging.getLogger(__name__)
API_VERSION = 1


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
        self.client = client or MennekesModbusClient(
            config.serial, write_state=WriteState.cold_start(time.monotonic())
        )
        self.state_path = state_path
        self.identity = DeviceIdentity()
        self.data = WallboxData()
        self.diagnostics = WriteDiagnostics()
        self.connection = ConnectionState(available=False)
        self.heartbeat = HeartbeatTask(self.client, self.diagnostics)
        self.control: AmtronControl | None = None
        self._poller: DevicePoller | None = None
        self._store: IntentStore | None = None
        self._poll_lock = asyncio.Lock()
        self._store_lock = asyncio.Lock()
        self._logged_unavailable = False
        self._command_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_started = asyncio.Event()
        self._success = False
        self._attempts = 0
        self._retry_at = 0.0
        self._updated_at: float | None = None
        self._stopping = False
        self._phase = "opening serial connection"
        self.heartbeat.async_add_listener(self._log_heartbeat_health)

    def start(self) -> asyncio.Task[None]:
        _LOGGER.info("Opening serial connection to the wallbox")
        self._task = asyncio.create_task(self._run(), name="amtron_service")
        return self._task

    def _log_heartbeat_health(self) -> None:
        if self._stopping:
            return
        if self.heartbeat.healthy is True:
            _LOGGER.info("Heartbeat acknowledged by the wallbox")
        elif self.heartbeat.healthy is False:
            _LOGGER.warning(
                "Heartbeat acknowledgement missing; checking the serial connection"
            )

    async def _initialize(self) -> None:
        self._phase = "opening serial connection"
        await self.client.async_connect()
        self._phase = "reading wallbox identity"
        identity = await async_read_identity(self.client)
        store = IntentStore(
            self.state_path,
            identity.serial_number
            or f"{self.config.serial.port}:{self.config.serial.device_id}",
        )
        self._phase = "loading saved control intent"
        await asyncio.to_thread(store.load, self.client.write_state)
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
            options=ControlOptions(self.config.read_only, self.config.current_cap),
            state=lambda: self.data,
            request_refresh=self.refresh,
        )
        await self.refresh()
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
        _LOGGER.info("Serial connection established; wallbox initialized")
        if not self.config.read_only:
            self._heartbeat_task = self.heartbeat.start()
            self._heartbeat_started.set()
            _LOGGER.info("Heartbeat started")
        else:
            _LOGGER.info("Heartbeat disabled in read-only mode")

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
                await self._persist()
                if self._logged_unavailable:
                    _LOGGER.info("Wallbox communication restored")
                self._logged_unavailable = False
            except (AmtronBusError, DeviceError, OSError, ValueError) as err:
                self._success = False
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
            try:
                self.data = await self._poller.async_poll(
                    self.data, last_success=self._success
                )
            except AmtronBusError:
                self._success = False
                raise
            self._success = True
            self._updated_at = time.monotonic()

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

    async def _persist(self) -> None:
        if self._store is not None:
            async with self._store_lock:
                self._phase = "saving control intent"
                original = self.client.write_state
                state = WriteState(
                    charging_setpoint=original.charging_setpoint,
                    pause_requested=original.pause_requested,
                    charging_release_requested=original.charging_release_requested,
                )
                saving = asyncio.create_task(asyncio.to_thread(self._store.save, state))
                try:
                    await asyncio.shield(saving)
                except asyncio.CancelledError:
                    await saving
                    raise

    def snapshot(self) -> dict[str, Any]:
        control = self.control
        return {
            "api_version": API_VERSION,
            "identity": asdict(self.identity),
            "available": self._success
            and self._updated_at is not None
            and time.monotonic() - self._updated_at
            <= max(30, self.config.scan_interval * 3),
            "values": self.data.values,
            "failed_blocks": list(self.data.failed_blocks),
            "current_write_generation": self.data.current_write_generation,
            "control": {
                "read_only": self.config.read_only,
                "current_cap": self.config.current_cap,
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

    async def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.keys() - {"command", "value", "allow_unlimited", "current_cap"}:
            raise ValueError("unknown command field")
        async with self._command_lock:
            control = self.control
            if control is None or not self._success:
                raise DeviceError("service_unavailable")
            command = payload.get("command")
            value = payload.get("value")
            try:
                if command == "set_current":
                    unlimited = payload.get("allow_unlimited", False)
                    if not isinstance(unlimited, bool):
                        raise ValueError("allow_unlimited must be boolean")
                    await control.async_set_charging_current(
                        number(value, 0, 32), allow_unlimited=unlimited
                    )
                elif command == "pause":
                    await control.async_pause_charging()
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
                await self._persist()
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
                    await self.control.async_shutdown()
                await self._persist()
        finally:
            await self.client.async_close()
            _LOGGER.info("Serial connection closed")
