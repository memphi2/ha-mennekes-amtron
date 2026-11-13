"""Optional service-console capabilities sharing the existing single bus owner."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .client import MennekesModbusClient
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

    async def apply_polling_intervals(self, scan: float, measurement: float) -> None:
        """Keep the bus owner, heartbeat, hold/backoff state and pending commands.

        A running poll or sleep is not interrupted. The next ordinary polling
        cycle uses the new intervals; this is not an immediate refresh command.
        """
        scan, measurement = number(scan, 2, 300), number(measurement, 2, 300)
        async with self._poll_lock:
            if self._stopping:
                raise DeviceError("service_unavailable")
            if self._poller is not None:
                self._poller.update_measurement_interval(measurement)
            self.config = replace(self.config, scan_interval=scan, measurement_interval=measurement)
