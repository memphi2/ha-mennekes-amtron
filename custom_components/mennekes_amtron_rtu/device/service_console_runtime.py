"""Optional service-console capabilities sharing the existing single bus owner."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .client import MennekesModbusClient
from .control import ControlOptions
from .errors import DeviceError
from .register_monitor import RegisterMonitor
from .service_config import ServerConfig, number
from .service_runtime import ModbusService


class ConsoleModbusService(ModbusService):
    """Add passive observations without changing the polling or write policy."""

    def __init__(
        self, config: ServerConfig, state_path: Path, *,
        client: MennekesModbusClient | None = None,
    ) -> None:
        super().__init__(config, state_path, client=client)
        self.register_monitor = RegisterMonitor()
        self.client.modbus_trace.register_monitor = self.register_monitor

    async def _initialize(self) -> None:
        self.register_monitor.clear()
        await super()._initialize()

    async def apply_runtime_settings(self, candidate: ServerConfig) -> None:
        """Keep the bus owner, heartbeat, hold/backoff state and pending commands.

        A running poll or sleep is not interrupted; the next ordinary cycle
        uses the new intervals. New commands use the new limits at once, and
        the polling loop lowers a charging current above a lowered cap.
        """
        control = self.control
        if self._stopping or control is None:
            raise DeviceError("service_unavailable")
        cap = number(candidate.current_cap, 6, 32)
        options = ControlOptions(
            control.options.read_only, cap,
            state_change_interval_seconds=candidate.state_change_interval_seconds,
            phase_switch_grace_seconds=candidate.phase_switch_grace_seconds,
        )
        measurement = number(candidate.measurement_interval, 2, 300)
        scan = number(candidate.scan_interval, 2, 300)
        # The only wait. A command takes the control lock before it refreshes,
        # so this one never waits for the poll lock while holding it, and a
        # timeout here leaves nothing half applied.
        await control.async_set_limits(options)
        if self._poller is not None:
            self._poller.update_measurement_interval(measurement)
        self.config = replace(
            self.config, scan_interval=scan, measurement_interval=measurement,
            current_cap=cap,
            state_change_interval_seconds=options.state_change_interval_seconds,
            phase_switch_grace_seconds=options.phase_switch_grace_seconds,
        )
