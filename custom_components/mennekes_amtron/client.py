"""Serial Modbus client for one AMTRON wallbox.

RS-485 is a single-master bus and this integration shares it between the data
poll, the heartbeat task and user-triggered writes. Every transaction
therefore passes through one lock in this module; nothing else may talk to
pymodbus.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient

from . import _client_read, _client_write
from .client_errors import AmtronConnectionError, AmtronRateLimitedError
from .const import (
    DEFAULT_BAUDRATE,
    DEFAULT_BYTESIZE,
    DEFAULT_DEVICE_ID,
    DEFAULT_PARITY,
    DEFAULT_RETRIES,
    DEFAULT_STOPBITS,
    DEFAULT_TIMEOUT,
)
from .decode import RegisterValue, decode_register
from .register_blocks import RegisterBlock
from .registers import RegisterSpec

_LOGGER = logging.getLogger(__name__)

type ClientFactory = Callable[["SerialConfig"], Any]


@dataclass(frozen=True, slots=True)
class SerialConfig:
    """Serial bus parameters of one wallbox.

    The defaults are the vendor's factory settings; all of them can be
    changed with the MENNEKES configuration tool, so all of them are
    configurable here too.
    """

    port: str
    baudrate: int = DEFAULT_BAUDRATE
    bytesize: int = DEFAULT_BYTESIZE
    parity: str = DEFAULT_PARITY
    stopbits: int = DEFAULT_STOPBITS
    device_id: int = DEFAULT_DEVICE_ID
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES


def create_serial_client(config: SerialConfig) -> AsyncModbusSerialClient:
    """Create the pymodbus client for a serial configuration."""

    return AsyncModbusSerialClient(
        config.port,
        framer=FramerType.RTU,
        baudrate=config.baudrate,
        bytesize=config.bytesize,
        parity=config.parity,
        stopbits=config.stopbits,
        timeout=config.timeout,
        retries=config.retries,
    )


class MennekesModbusClient:
    """Serialized Modbus access to one wallbox."""

    def __init__(
        self,
        config: SerialConfig,
        *,
        client_factory: ClientFactory = create_serial_client,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client_factory = client_factory
        self._monotonic = monotonic
        self._client: Any | None = None
        self._lock = asyncio.Lock()
        self._rate_limiter = _client_write.WriteRateLimiter()

    @property
    def config(self) -> SerialConfig:
        """Return the serial configuration this client was built with."""

        return self._config

    @property
    def connected(self) -> bool:
        """Return true while the serial transport is open."""

        if self._client is None:
            return False
        return bool(getattr(self._client, "connected", False))

    async def async_connect(self) -> None:
        """Open the serial port, or raise when the port cannot be used."""

        async with self._lock:
            if self.connected:
                return
            client = self._client or self._client_factory(self._config)
            self._client = client
            try:
                connected = await client.connect()
            # pyserial raises many unrelated OSError subclasses; all of them
            # mean the same thing here, and all of them are re-raised as one.
            except Exception as err:
                raise AmtronConnectionError(
                    f"cannot open {self._config.port}: {err}"
                ) from err
            if not connected:
                raise AmtronConnectionError(f"cannot open {self._config.port}")
            _LOGGER.debug("Opened %s", self._config.port)

    async def async_close(self) -> None:
        """Close the serial port and forget the transport."""

        async with self._lock:
            client = self._client
            self._client = None
            if client is None:
                return
            close = getattr(client, "close", None)
            if close is None:
                return
            result = close()
            if asyncio.iscoroutine(result):
                await result

    async def async_read_block(self, block: RegisterBlock) -> dict[str, RegisterValue]:
        """Read one register block."""

        async with self._lock:
            client = self._require_client()
            return await _client_read.read_block(
                client, block=block, device_id=self._config.device_id
            )

    async def async_read_register(self, spec: RegisterSpec) -> RegisterValue:
        """Read one register range."""

        async with self._lock:
            client = self._require_client()
            words = await _client_read.read_words(
                client,
                address=spec.address,
                count=spec.count,
                device_id=self._config.device_id,
            )
        return decode_register(spec, words)

    async def async_read_words(self, *, address: int, count: int) -> list[int]:
        """Read raw register words, used by diagnostics."""

        async with self._lock:
            client = self._require_client()
            return await _client_read.read_words(
                client,
                address=address,
                count=count,
                device_id=self._config.device_id,
            )

    async def async_write_register(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
    ) -> None:
        """Write one register range, honouring its minimum write interval."""

        async with self._lock:
            now = self._monotonic()
            remaining = self._rate_limiter.remaining(
                spec.key, now=now, min_interval=min_interval
            )
            if remaining > 0:
                raise AmtronRateLimitedError(spec.key, remaining)
            client = self._require_client()
            self._rate_limiter.record(spec.key, now=now)
            try:
                await _client_write.write_register(
                    client,
                    spec=spec,
                    value=value,
                    device_id=self._config.device_id,
                )
            except Exception:
                self._rate_limiter.forget(spec.key)
                raise

    def _require_client(self) -> Any:
        if self._client is None:
            raise AmtronConnectionError(f"{self._config.port} is not connected")
        return self._client
