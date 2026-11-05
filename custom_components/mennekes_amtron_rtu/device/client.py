"""Serialized Modbus client for one AMTRON wallbox over serial or TCP.

Modbus RTU uses a single master, and this integration shares its connection
between the data poll, the heartbeat task and user-triggered writes. Every transaction
therefore passes through one lock in this module; nothing else may talk to
pymodbus.

One lock alone is not enough, because it is fair: a heartbeat queues behind
everything already waiting, and a stalling device makes every wait a full
timeout plus its retries. Bulk users therefore pass a second gate first, so
only one of them can ever sit in front of the heartbeat.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from functools import partial

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from . import _client_read, _client_write
from .client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronRateLimitedError,
    AmtronWriteRejectedError,
)
from .connection import ConnectionConfig, NetworkConfig
from .connection import SerialConfig as SerialConfig
from .const import (
    MIN_CURRENT_WRITE_INTERVAL_SECONDS,
)
from .decode import RegisterValue, decode_register
from .register_blocks import RegisterBlock
from .registers import RegisterSpec
from .transport import ModbusTransport
from .write_state import WriteState
from .write_trace import WriteTrace

_LOGGER = logging.getLogger(__name__)

type ClientFactory = Callable[[ConnectionConfig], ModbusTransport]


def create_serial_client(
    config: SerialConfig,
    *,
    trace_packet: Callable[[bool, bytes], bytes] | None = None,
) -> AsyncModbusSerialClient:
    """Create the pymodbus client for a serial configuration."""

    return AsyncModbusSerialClient(
        config.port,
        framer=FramerType.RTU,
        baudrate=config.baudrate,
        bytesize=config.bytesize,
        parity=config.parity,
        stopbits=config.stopbits,
        timeout=config.timeout,
        # A lost reply does not prove that a write was not applied. Library
        # retries would resend it after one second, bypassing our write floor.
        # Only reads may retry, explicitly in _client_read.read_words.
        retries=0,
        trace_packet=trace_packet,
    )


def create_modbus_client(
    config: ConnectionConfig,
    *,
    trace_packet: Callable[[bool, bytes], bytes] | None = None,
) -> ModbusTransport:
    """Select the wire framing while retaining one serialized command path."""
    if isinstance(config, SerialConfig):
        return create_serial_client(config, trace_packet=trace_packet)
    return AsyncModbusTcpClient(
        config.host,
        port=config.tcp_port,
        framer=FramerType.RTU if config.connection_type == "rtu_over_tcp" else FramerType.SOCKET,
        timeout=config.timeout,
        retries=0,
        # Polling and heartbeat already reconnect through our lock. An
        # independent library reconnect can race them and leak a TCP socket.
        reconnect_delay=0,
        trace_packet=trace_packet,
    )


class MennekesModbusClient:
    """Serialized Modbus access to one wallbox."""

    def __init__(
        self,
        config: ConnectionConfig,
        *,
        client_factory: ClientFactory = create_modbus_client,
        monotonic: Callable[[], float] = time.monotonic,
        write_state: WriteState | None = None,
    ) -> None:
        self._config = config
        self._monotonic = monotonic
        self.write_trace = WriteTrace(
            clock=monotonic, packet_trace=client_factory is create_modbus_client
        )
        self._client_factory: ClientFactory = (
            partial(create_modbus_client, trace_packet=self.write_trace.packet)
            if client_factory is create_modbus_client else client_factory
        )
        self._client: ModbusTransport | None = None
        self._lock = asyncio.Lock()
        # Bulk users queue behind this gate before they reach the bus, so at
        # most one of them is ever waiting on the bus itself. The heartbeat
        # skips the gate: it is the one transaction with a deadline, and
        # queueing it behind every reader is how it misses that deadline.
        self._queue = asyncio.Lock()
        self.write_state = write_state if write_state is not None else WriteState()
        self._rate_limiter = self.write_state.limiter

    @property
    def config(self) -> ConnectionConfig:
        """Return the connection configuration this client was built with."""

        return self._config

    @property
    def connected(self) -> bool:
        """Return true while the configured transport is open."""

        if self._client is None:
            return False
        return self._client.connected

    async def async_connect(self) -> None:
        """Open the configured connection or raise when it cannot be used."""

        async with self._lock:
            client = self._client or self._client_factory(self._config)
            self._client = client
            await self._async_connect_locked(client)

    async def _async_connect_locked(self, client: ModbusTransport) -> None:
        """Open the transport while the caller holds the transaction lock."""

        if client.connected:
            return
        try:
            connected = await client.connect()
        # Drivers raise unrelated exception types; callers handle one bus error.
        except Exception as err:
            raise AmtronConnectionError(
                f"cannot open {self._config.port}: {err}"
            ) from err
        if not connected:
            raise AmtronConnectionError(f"cannot open {self._config.port}")
        _LOGGER.debug("Opened %s", self._config.port)

    async def _async_transaction_client(self) -> ModbusTransport:
        """Recover a dropped TCP link before the next serialized transaction.

        Never recreate a client after explicit close, and never replay a write
        whose reply was lost. Only a newly requested transaction reconnects.
        """

        client = self._require_client()
        if isinstance(self._config, NetworkConfig):
            await self._async_connect_locked(client)
        return client

    async def async_close(self) -> None:
        """Close the connection and forget the transport."""

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

        async with self._queue:
            client = self._require_client()
            return await _client_read.read_block(
                client,
                block=block,
                device_id=self._config.device_id,
                read=self._async_read_words_locked,
            )

    async def _async_read_words_locked(self, address: int, count: int) -> list[int]:
        """Release the bus between fallback reads so a heartbeat can pass."""

        async with self._lock:
            return await _client_read.read_words(
                await self._async_transaction_client(),
                address=address,
                count=count,
                device_id=self._config.device_id,
                retries=self._config.retries,
            )

    async def async_read_register(self, spec: RegisterSpec) -> RegisterValue:
        """Read one register range."""

        async with self._queue, self._lock:
            client = await self._async_transaction_client()
            words = await _client_read.read_words(
                client,
                address=spec.address,
                count=spec.count,
                device_id=self._config.device_id,
                retries=self._config.retries,
            )
        return decode_register(spec, words)

    async def async_read_words(self, *, address: int, count: int) -> list[int]:
        """Read raw register words, used by diagnostics."""

        async with self._queue, self._lock:
            client = await self._async_transaction_client()
            return await _client_read.read_words(
                client,
                address=address,
                count=count,
                device_id=self._config.device_id,
                retries=self._config.retries,
            )

    async def async_write_register(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
        priority: bool = False,
    ) -> None:
        """Write one register range, honouring its minimum write interval.

        A priority write skips the queue the readers share. Only the
        heartbeat uses it, because only the heartbeat has a deadline the
        device enforces by faulting.
        """

        queued = self._monotonic()
        if priority:
            await self._async_write_locked(spec, value, min_interval=min_interval, queued=queued)
            return
        async with self._queue:
            await self._async_write_locked(spec, value, min_interval=min_interval, queued=queued)

    def check_write_interval(
        self, spec: RegisterSpec, *, min_interval: float = 0.0
    ) -> None:
        """Reject a write still in cooldown, without reserving the register.

        Compound operations can check before changing other registers. They
        must serialize their callers; the actual write checks again on the bus.
        """

        if spec.key == "charging_current_ems":
            min_interval = max(min_interval, MIN_CURRENT_WRITE_INTERVAL_SECONDS)
        remaining = self._rate_limiter.remaining(
            spec.key, now=self._monotonic(), min_interval=min_interval
        )
        if remaining > 0:
            raise AmtronRateLimitedError(spec.key, remaining)

    async def _async_write_locked(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float,
        queued: float,
    ) -> None:
        async with self._lock:
            self.check_write_interval(spec, min_interval=min_interval)
            client = self._require_client()
            rejected = False
            error: BaseException | None = None
            self.write_trace.begin(
                address=spec.address, queued=queued, started=self._monotonic()
            )
            try:
                client = await self._async_transaction_client()
                await _client_write.write_register(
                    client,
                    spec=spec,
                    value=value,
                    device_id=self._config.device_id,
                )
            except (Exception, asyncio.CancelledError) as err:
                error = err
                # A definite rejection did not change the register. A missing
                # reply or transport failure may follow an applied write, so
                # those retain the interval, as does cancellation.
                if isinstance(err, (AmtronWriteRejectedError, AmtronIllegalAddressError)):
                    self._rate_limiter.forget(spec.key)
                    rejected = True
                raise
            finally:
                self.write_trace.finish(error=error, connected=self.connected)
                if not rejected:
                    if spec.key == "charging_current_ems":
                        self.write_state.current_write_generation += 1
                    # Connect/send/response delays belong to this transaction.
                    # Even cancellation can follow an applied write. Measuring
                    # from completion conservatively protects the wire interval.
                    self._rate_limiter.record(spec.key, now=self._monotonic())

    def _require_client(self) -> ModbusTransport:
        if self._client is None:
            raise AmtronConnectionError(f"{self._config.port} is not connected")
        return self._client
