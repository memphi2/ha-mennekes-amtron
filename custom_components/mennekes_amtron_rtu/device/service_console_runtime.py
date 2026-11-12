"""Optional service-console capabilities sharing the existing single bus owner."""

from __future__ import annotations

from pathlib import Path

from .client import MennekesModbusClient
from .register_monitor import RegisterMonitor
from .service_config import ServerConfig
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
