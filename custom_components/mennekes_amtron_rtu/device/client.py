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
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

from pymodbus import FramerType
from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient

from . import _client_read, _client_write
from .client_errors import (
    AmtronBusError,
    AmtronConnectionError,
    AmtronIdentityError,
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
from .link_quality import LinkQuality
from .modbus_trace import ModbusTrace
from .register_blocks import RegisterBlock
from .registers import HEARTBEAT, SERIAL_NUMBER, RegisterSpec
from .transport import ModbusTransport
from .waveshare_auth import WaveshareAuthenticationRejectedError
from .waveshare_transport import WaveshareModbusTcpClient
from .write_state import WriteState
from .write_trace import WriteTrace

_LOGGER = logging.getLogger(__name__)

type ClientFactory = Callable[[ConnectionConfig], ModbusTransport]
type LockedRegisterReader = Callable[[RegisterSpec], Awaitable[RegisterValue]]
type WritePrecondition = Callable[[LockedRegisterReader], Awaitable[None]]


@dataclass(slots=True)
class ConnectionConfigurationGuard:
    """Explicit proof that a failed gateway change restored the old settings."""

    _reconnect_allowed: bool = False

    def allow_reconnect(self) -> None:
        """Call only after verified rollback or a failure before any mutation."""
        self._reconnect_allowed = True

    @property
    def reconnect_allowed(self) -> bool:
        """Whether an exceptional exit can safely resume the old connection."""
        return self._reconnect_allowed


async def _async_close_transport(client: ModbusTransport | None) -> None:
    if client is None or (close := getattr(client, "close", None)) is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


def create_serial_client(
    config: SerialConfig,
    *,
    trace_packet: Callable[[bool, bytes], bytes] | None = None,
    trace_connect: Callable[[bool], None] | None = None,
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
        trace_connect=trace_connect,
    )


def create_modbus_client(
    config: ConnectionConfig,
    *,
    trace_packet: Callable[[bool, bytes], bytes] | None = None,
    trace_connect: Callable[[bool], None] | None = None,
) -> ModbusTransport:
    """Select the wire framing while retaining one serialized command path."""
    if isinstance(config, SerialConfig):
        return create_serial_client(
            config, trace_packet=trace_packet, trace_connect=trace_connect
        )
    if config.tcp_authentication:
        return WaveshareModbusTcpClient(
            config, trace_packet=trace_packet, trace_connect=trace_connect,
        )
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
        trace_connect=trace_connect,
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
        self.link_quality = LinkQuality(monotonic=monotonic)
        self.modbus_trace = ModbusTrace(clock=monotonic)
        self.write_trace = WriteTrace(
            clock=monotonic, packet_trace=client_factory is create_modbus_client
        )
        self._bound_serial: str | None = None
        self._identity_check_needed = False
        self._connection_generation = 0
        connection_changed = weakref.WeakMethod(self._connection_changed)

        def trace_connect(connected: bool) -> None:
            # The stored factory and transport must not keep their owner alive.
            if (callback := connection_changed()) is not None:
                callback(connected)

        self._trace_connect = trace_connect
        self._client_factory: ClientFactory = (
            partial(
                create_modbus_client, trace_packet=self.write_trace.packet,
                trace_connect=self._trace_connect,
            )
            if client_factory is create_modbus_client else client_factory
        )
        self._client: ModbusTransport | None = None
        self._configuration_pending_restart = False
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

        if self._client is None or self._configuration_pending_restart:
            return False
        return self._client.connected

    @property
    def identity_verified(self) -> bool:
        """Whether the current connection still belongs to the bound device."""

        return self._bound_serial is None or not self._identity_check_needed

    def bind_identity(self, serial_number: str | None) -> None:
        """Bind a readable physical serial once, never silently replace it."""

        if self._bound_serial is not None and serial_number != self._bound_serial:
            self._identity_check_needed = True
            raise AmtronIdentityError("wallbox identity changed or is unavailable")
        self._bound_serial = serial_number
        # The link may have changed after the serial's setup read but before
        # the remaining capability reads completed. Verify the first operation.
        self._identity_check_needed = serial_number is not None

    def _connection_changed(self, _connected: bool) -> None:
        """Invalidate identity on both explicit and library-managed reconnects."""

        self._identity_check_needed = True
        self._connection_generation += 1

    async def _async_verify_identity(self, client: ModbusTransport) -> None:
        """Confirm the bound serial while holding the transaction lock."""

        if self._bound_serial is None:
            return
        # Serial reconnect can return before its connection-made callback runs.
        # If that callback overlaps the read, discard the old-session result and
        # allow one bounded fresh check. No command is sent before it succeeds.
        for _attempt in range(2):
            generation = self._connection_generation
            try:
                words = await _client_read.read_words(
                    client, address=SERIAL_NUMBER.address, count=SERIAL_NUMBER.count,
                    device_id=self._config.device_id, retries=self._config.retries,
                    observe=self.link_quality.observe, trace=self.modbus_trace,
                )
            except asyncio.CancelledError as err:
                # No write was attempted. Preserve cancellation, but distinguish
                # this from an acknowledgement lost after an actual command.
                self._identity_check_needed = True
                raise err from AmtronIdentityError("wallbox identity verification cancelled")
            except AmtronBusError as err:
                self._identity_check_needed = True
                raise AmtronIdentityError("wallbox identity could not be verified") from err
            if generation != self._connection_generation:
                continue
            if decode_register(SERIAL_NUMBER, words) != self._bound_serial:
                break
            self._identity_check_needed = False
            return
        self._identity_check_needed = True
        raise AmtronIdentityError("wallbox identity changed or is unavailable")

    async def async_connect(self) -> None:
        """Open the configured connection or raise when it cannot be used."""

        async with self._lock:
            self._require_connection_usable()
            client = self._client or self._client_factory(self._config)
            self._client = client
            await self._async_connect_locked(client)

    async def _async_connect_locked(self, client: ModbusTransport) -> None:
        """Open the transport while the caller holds the transaction lock."""

        self._require_connection_usable()
        if client.connected:
            return
        self._identity_check_needed = True
        try:
            connected = await client.connect()
        except WaveshareAuthenticationRejectedError as err:
            # Preserve actionable credential failures for HA's reauth flow.
            self.link_quality.observe(err)
            raise
        # Drivers raise unrelated exception types; callers handle one bus error.
        except Exception as err:
            self.link_quality.observe(AmtronConnectionError("cannot open transport"))
            raise AmtronConnectionError(
                f"cannot open {self._config.port}: {err}"
            ) from err
        if not connected:
            self.link_quality.observe(AmtronConnectionError("cannot open transport"))
            raise AmtronConnectionError(f"cannot open {self._config.port}")
        _LOGGER.debug("Opened %s", self._config.port)

    async def _async_transaction_client(
        self, *, verify_identity: bool = False
    ) -> ModbusTransport:
        """Recover a dropped TCP link before the next serialized transaction.

        Never recreate a client after explicit close, and never replay a write
        whose reply was lost. Only a newly requested transaction reconnects.
        """

        client = self._require_client()
        if isinstance(self._config, NetworkConfig):
            await self._async_connect_locked(client)
        if self._identity_check_needed or verify_identity:
            await self._async_verify_identity(client)
        return client

    async def async_close(self) -> None:
        """Close the connection and forget the transport."""

        async with self._lock:
            client = self._client
            self._client = None
            await _async_close_transport(client)

    @asynccontextmanager
    async def async_connection_configuration(self) -> AsyncIterator[ConnectionConfigurationGuard]:
        """Exclude bus traffic while explicitly applying gateway settings.

        Successful changes require a new client with the saved settings after
        service restart. Failed or cancelled changes remain blocked unless the
        caller explicitly confirms that rollback restored the old connection.
        """
        async with self._lock:
            was_pending = self._configuration_pending_restart
            self._configuration_pending_restart = True
            self._identity_check_needed = True
            guard = ConnectionConfigurationGuard()
            try:
                await _async_close_transport(self._client)
                yield guard
            except BaseException:
                if guard.reconnect_allowed:
                    self._configuration_pending_restart = was_pending
                raise

    async def async_read_block(self, block: RegisterBlock) -> dict[str, RegisterValue]:
        """Read one register block."""

        async with self._queue:
            client = self._require_client()
            result = await _client_read.read_block(
                client,
                block=block,
                device_id=self._config.device_id,
                read=self._async_read_words_locked,
            )
            if (
                self._bound_serial is not None
                and SERIAL_NUMBER.key in result
                and result[SERIAL_NUMBER.key] != self._bound_serial
            ):
                self._identity_check_needed = True
                raise AmtronIdentityError("wallbox identity changed or is unavailable")
            return result

    async def _async_read_words_locked(
        self, address: int, count: int, *, verify_identity: bool = False,
    ) -> list[int]:
        """Give a heartbeat the bus between identity and a following read.

        The caller retains the ordinary-operation queue. A read after a new
        connection must not consume identity evidence from a different link.
        """

        async with self._lock:
            previous = self._require_client()
            old_generation = self._connection_generation
            checking_identity = verify_identity or self._identity_check_needed or not previous.connected
            client = await self._async_transaction_client(verify_identity=verify_identity)
            generation = self._connection_generation
            bound_serial = self._bound_serial
            if bound_serial is None or not (checking_identity or generation != old_generation):
                return await _client_read.read_words(
                    client,
                    address=address,
                    count=count,
                    device_id=self._config.device_id,
                    retries=self._config.retries,
                    observe=self.link_quality.observe, trace=self.modbus_trace,
                )

        # Identity and the target are separate Modbus transactions. Keep the
        # queue, but let an already waiting heartbeat acquire the bus first.
        await asyncio.sleep(0)
        async with self._lock:
            if (
                self._require_client() is not client
                or self._connection_generation != generation
                or self._bound_serial != bound_serial
                or not client.connected
                or not self.identity_verified
            ):
                raise AmtronConnectionError("connection changed during read")
            return await _client_read.read_words(
                client,
                address=address,
                count=count,
                device_id=self._config.device_id,
                retries=self._config.retries,
                observe=self.link_quality.observe, trace=self.modbus_trace,
            )

    async def async_read_register(
        self, spec: RegisterSpec, *, verify_identity: bool = False,
    ) -> RegisterValue:
        """Read one register range."""

        async with self._queue:
            words = await self._async_read_words_locked(
                spec.address, spec.count, verify_identity=verify_identity,
            )
        return decode_register(spec, words)

    async def async_read_words(self, *, address: int, count: int) -> list[int]:
        """Read raw register words, used by diagnostics."""

        async with self._queue:
            return await self._async_read_words_locked(address, count)

    async def async_write_register(
        self,
        spec: RegisterSpec,
        value: float,
        *,
        min_interval: float = 0.0,
        priority: bool = False,
        before_write: Callable[[], None] | None = None,
        precondition: WritePrecondition | None = None,
    ) -> None:
        """Write one register range, honouring its minimum write interval.

        A priority write skips the queue the readers share. Only the
        heartbeat uses it, because only the heartbeat has a deadline the
        device enforces by faulting.
        """

        queued = self._monotonic()
        if priority:
            await self._async_write_locked(
                spec, value, min_interval=min_interval, queued=queued,
                verify_identity=spec != HEARTBEAT, before_write=before_write,
                precondition=precondition,
            )
            return
        async with self._queue:
            await self._async_write_locked(
                spec, value, min_interval=min_interval, queued=queued,
                verify_identity=True, before_write=before_write,
                precondition=precondition,
            )

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
        verify_identity: bool,
        before_write: Callable[[], None] | None = None,
        precondition: WritePrecondition | None = None,
    ) -> None:
        await self._lock.acquire()
        locked = True
        try:
            self.check_write_interval(spec, min_interval=min_interval)
            client = self._require_client()
            if before_write is not None or precondition is not None:
                # A scheduled command can expire while waiting for this bus or
                # for identity verification. A rejected guard sends no write,
                # consumes no write interval and is not a transport failure.
                client = await self._async_transaction_client(verify_identity=verify_identity)
                if before_write is not None:
                    before_write()
                if precondition is not None:
                    generation = self._connection_generation

                    async def read_locked(register: RegisterSpec) -> RegisterValue:
                        nonlocal locked
                        # Keep the ordinary-operation queue, but let a pending
                        # priority heartbeat pass between Modbus transactions.
                        # The final read remains locked through the write.
                        self._lock.release()
                        locked = False
                        await asyncio.sleep(0)
                        await self._lock.acquire()
                        locked = True
                        if (
                            self._require_client() is not client
                            or generation != self._connection_generation
                            or not client.connected
                        ):
                            raise AmtronConnectionError("connection changed during write precondition")
                        words = await _client_read.read_words(
                            client,
                            address=register.address,
                            count=register.count,
                            device_id=self._config.device_id,
                            retries=self._config.retries,
                            observe=self.link_quality.observe, trace=self.modbus_trace,
                        )
                        return decode_register(register, words)

                    await precondition(read_locked)
                    if (
                        self._require_client() is not client
                        or generation != self._connection_generation
                        or not client.connected
                    ):
                        # Never apply an observation made on a previous link to
                        # a write after a transparent disconnect or reconnect.
                        raise AmtronConnectionError("connection changed during write precondition")
                    # A priority operation may have run while the bus was free.
                    self.check_write_interval(spec, min_interval=min_interval)
                    # The precondition may await one or more live reads while
                    # deadline or goal evidence changes in another task.
                    if before_write is not None:
                        before_write()
            rejected = False
            error: BaseException | None = None
            self.write_trace.begin(
                address=spec.address, queued=queued, started=self._monotonic()
            )
            try:
                # A transparent gateway may retain its TCP connection while
                # its downstream wallbox changes. Check every control write;
                # ordinary heartbeats reuse the connection's confirmed identity.
                if before_write is None and precondition is None:
                    client = await self._async_transaction_client(
                        verify_identity=verify_identity
                    )
                await _client_write.write_register(
                    client,
                    spec=spec,
                    value=value,
                    device_id=self._config.device_id,
                    trace=self.modbus_trace,
                )
            except (Exception, asyncio.CancelledError) as err:
                error = err
                # A definite rejection did not change the register. A missing
                # reply or transport failure may follow an applied write, so
                # those retain the interval, as does cancellation.
                if (
                    isinstance(err, (AmtronWriteRejectedError, AmtronIllegalAddressError, AmtronIdentityError))
                    or isinstance(err.__cause__, AmtronIdentityError)
                ):
                    self._rate_limiter.forget(spec.key)
                    rejected = True
                raise
            finally:
                self.write_trace.finish(error=error, connected=self.connected)
                self.link_quality.observe(error)
                if not rejected:
                    if spec.key == "charging_current_ems":
                        self.write_state.current_write_generation += 1
                    # Connect/send/response delays belong to this transaction.
                    # Even cancellation can follow an applied write. Measuring
                    # from completion conservatively protects the wire interval.
                    self._rate_limiter.record(spec.key, now=self._monotonic())
        finally:
            # Cancellation while reacquiring must not release a heartbeat's
            # lock. Once reacquired this owner alone releases it here.
            if locked:
                self._lock.release()

    def _require_client(self) -> ModbusTransport:
        self._require_connection_usable()
        if self._client is None:
            raise AmtronConnectionError(f"{self._config.port} is not connected")
        return self._client

    def _require_connection_usable(self) -> None:
        if self._configuration_pending_restart:
            raise AmtronConnectionError("gateway configuration requires a service restart")
