"""Explicit, reversible setup of the verified Waveshare wired adapter profile.

Never called by the normal Modbus connection path. UDP management is unicast to
one resolved IPv4 address; model identification additionally reads its web page.
The vendor's 167-byte parameter layout defines baud/parity/data/protocol fields
and firmware ``383 + ver``. SDK PARAM_STOP_BIT=261 maps to bit 0 at 0x72; the
verified 1.523 TCP authentication flag is bit 1 there. Unknown bytes are retained.
A full apply restarts the adapter and selects a passive request/reply profile.
No password-change command is implemented.
"""

# Private native images deliberately stay inside this module.
# ruff: noqa: SLF001

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Self
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary, WeakValueDictionary

import aiohttp

from .client_errors import AmtronConnectionError
from .connection import (
    validate_network_host,
    validate_tcp_auth_password,
    validate_tcp_port,
)
from .const import SUPPORTED_FRAMES
from .waveshare_auth import (
    WaveshareAuthenticationError,
    WaveshareAuthenticationRejectedError,
    async_open_authenticated_socket,
)

GATEWAY_BAUDRATES = (9600, 14400, 19200, 28800, 38400, 57600)
_MODELS = frozenset(("RS485 TO ETH (B)", "RS485 TO ETH (C)", "RS485 TO POE ETH (B)"))
_FIRMWARE = "1.523"
_PARAMETER_SIZE = 167
_MANAGEMENT_PORT = 1092
_QUERY_TIMEOUT = 2.0
_APPLY_TIMEOUT = 12.0
_ROLLBACK_TIMEOUT = 35.0
_AUTH_TIMEOUT = 8.0
_BAUDRATES = (1200, 2400, 4800, 7200, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200, 230400, 460800)
_PARITY = "NOEMS"
_BIT_FIELDS = frozenset((0x6E, 0x72))
# Function bits: data-triggered restart, parameter upload, MAC preamble, and
# replay of buffered serial bytes. P2P/ping/broadcast-filter settings are retained.
_AUTONOMOUS_FLAGS = 0xA3
_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, WeakValueDictionary[str, asyncio.Lock]] = WeakKeyDictionary()


class GatewayError(AmtronConnectionError):
    """A sanitized setup failure; never include native packets or credentials."""

    def __init__(self, reason: str = "gateway_unreachable") -> None:
        super().__init__(reason)
        self.reason = reason


class GatewayUnsupportedError(GatewayError):
    def __init__(self) -> None:
        super().__init__("gateway_unsupported")


class GatewayConflictError(GatewayError):
    def __init__(self) -> None:
        super().__init__("gateway_changed")


class GatewayRollbackError(GatewayError):
    def __init__(self) -> None:
        super().__init__("gateway_rollback_failed")


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    """Select connection fields and apply the known passive request/reply profile.

    None preserves that selected field. Every explicit setup also disables the
    documented polling, preamble/replay and flow-control features below.
    """

    baudrate: int | None = None
    frame: str | None = None
    connection_type: str | None = None
    tcp_authentication: bool | None = None
    tcp_port: int | None = None

    def __post_init__(self) -> None:
        if self.baudrate is not None and (type(self.baudrate) is not int or self.baudrate not in GATEWAY_BAUDRATES):
            raise ValueError("Unsupported adapter baud rate")
        if self.frame is not None and self.frame not in SUPPORTED_FRAMES:
            raise ValueError("Unsupported wallbox frame")
        if self.connection_type is not None and self.connection_type not in ("rtu_over_tcp", "modbus_tcp"):
            raise ValueError("Unsupported adapter conversion mode")
        if self.tcp_authentication is not None and type(self.tcp_authentication) is not bool:
            raise ValueError("Adapter authentication must be boolean")
        if self.tcp_port is not None:
            validate_tcp_port(self.tcp_port)


@dataclass(frozen=True, slots=True)
class GatewaySnapshot:
    """Recognized settings; the private native image must never be serialized."""

    address: str
    model: str
    firmware: str
    tcp_port: int
    baudrate: int
    frame: str
    connection_type: str
    tcp_authentication: bool
    can_configure: bool
    _native: bytes = field(repr=False)

    @property
    def identifier(self) -> str:
        """Vendor device identifier, deliberately not called a physical MAC."""
        return self._native[0x1F:0x25].hex(":")


def _snapshot(address: str, native: bytes) -> GatewaySnapshot:
    if (
        len(native) != _PARAMETER_SIZE
        or native[:4] != ipaddress.IPv4Address(address).packed
        or native[0x1F:0x25] in (bytes(6), b"\xff" * 6)
        or native[0x67] != 140 or native[0x68] != 0xBE or native[0x70] != 6
        or native[0x14] not in (0, 1, 2, 3) or native[0x3C] not in (0, 1)
        or native[0x25] >= len(_BAUDRATES) or native[0x30] >= len(_PARITY)
        or native[0x3B] > 3 or native[0x73:0x75] != b"\x09\x04"
        or native[0x79:0x7B] != b"\x0a\x04"
        # Unknown extension/preamble formats must not be silently preserved or
        # erased. The verified plain profile has only these two counter TLVs.
        or any(native[0x7F:]) or native[0x72] & ~3 or native[0x34] != 0
        or native[0x37] not in (0, 2, 3, 4) or native[0x39] not in (0, 1, 2, 3)
    ):
        raise GatewayUnsupportedError
    port = int.from_bytes(native[0x10:0x12], "big")
    web_port = int.from_bytes(native[0x62:0x64], "big")
    if not port or not web_port or port == web_port:
        raise GatewayUnsupportedError
    return GatewaySnapshot(
        address=address, model="", firmware=_FIRMWARE,
        tcp_port=port, baudrate=_BAUDRATES[native[0x25]],
        frame=f"{8 - native[0x3B]}{_PARITY[native[0x30]].lower()}{1 + (native[0x72] & 1)}",
        connection_type="modbus_tcp" if native[0x3C] else "rtu_over_tcp",
        tcp_authentication=bool(native[0x72] & 2),
        # Management-password writes are unverified. An enabled TCP login in
        # another work mode cannot be proven with the TCP-server handshake.
        can_configure=not bool(native[0x6E] & 4) and not (native[0x72] & 2 and native[0x14] != 0),
        _native=native,
    )


def _stable(native: bytes) -> bytes:
    value = bytearray(native)
    # Documented live TCP status and two verified traffic-counter TLVs are not
    # settings. Their exact tag/length is checked before normalizing values.
    value[0x3D] &= ~1
    value[0x75:0x79] = bytes(4)
    value[0x7B:0x7F] = bytes(4)
    return bytes(value)


def _assert_identity(before: GatewaySnapshot, after: GatewaySnapshot) -> None:
    if (before.address != after.address or before.identifier != after.identifier
        or (before.model and after.model and before.model != after.model)):
        raise GatewayConflictError


class _ModelPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._heading = False
        self.headings: list[str] = []
        self.vendor = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h2":
            self._heading = True
            self.headings.append("")
        if tag == "a":
            for key, value in attrs:
                if key == "href" and value and urlsplit(value).hostname in ("waveshare.com", "www.waveshare.com"):
                    self.vendor = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self._heading = False

    def handle_data(self, data: str) -> None:
        if self._heading:
            self.headings[-1] += data


async def _resolve_address(host: str) -> str:
    host = validate_network_host(host)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            async with asyncio.timeout(_QUERY_TIMEOUT):
                records = await asyncio.get_running_loop().getaddrinfo(host, _MANAGEMENT_PORT, family=socket.AF_INET, type=socket.SOCK_DGRAM)
            addresses = {item[4][0] for item in records}
            if len(addresses) != 1:
                raise GatewayUnsupportedError
            address = ipaddress.ip_address(addresses.pop())
        except (OSError, TimeoutError):
            raise GatewayError from None
    if not isinstance(address, ipaddress.IPv4Address) or address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
        raise GatewayUnsupportedError
    return str(address)


async def _query_native(address: str) -> GatewaySnapshot:
    try:
        async with asyncio.timeout(_QUERY_TIMEOUT):
            loop = asyncio.get_running_loop()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setblocking(False)
                await loop.sock_connect(sock, (address, _MANAGEMENT_PORT))
                await loop.sock_sendall(sock, b"ZL\x04" + bytes(_PARAMETER_SIZE))
                packet = await loop.sock_recv(sock, _PARAMETER_SIZE + 4)
        if len(packet) != _PARAMETER_SIZE + 3 or packet[:3] not in (b"ZL\x01", b"ZL\x02"):
            raise GatewayUnsupportedError
        return _snapshot(address, packet[3:])
    except (OSError, TimeoutError):
        raise GatewayError from None


async def _verify_model(snapshot: GatewaySnapshot) -> str:
    web_port = int.from_bytes(snapshot._native[0x62:0x64], "big")
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with (
            aiohttp.ClientSession(timeout=timeout, trust_env=False) as session,
            session.get(f"http://{snapshot.address}:{web_port}/index.html", allow_redirects=False) as response,
        ):
            if response.status != 200:
                raise GatewayUnsupportedError
            body = bytearray()
            async for chunk in response.content.iter_chunked(8192):
                body.extend(chunk)
                if len(body) > 65536:
                    raise GatewayUnsupportedError
        page = _ModelPage()
        page.feed(body.decode("utf-8", errors="replace"))
        headings = [heading.strip() for heading in page.headings]
        if not page.vendor or len(headings) != 1 or headings[0] not in _MODELS:
            raise GatewayUnsupportedError
        return headings[0]
    except (aiohttp.ClientError, OSError, TimeoutError):
        raise GatewayError from None
    except ValueError:
        raise GatewayUnsupportedError from None


async def inspect_gateway(host: str) -> GatewaySnapshot:
    """Read one configured endpoint; no scan, credentials or mutation."""
    address = await _resolve_address(host)
    snapshot = await _query_native(address)
    model = await _verify_model(snapshot)
    current = replace(await _query_native(address), model=model)
    _assert_identity(snapshot, current)
    if _stable(current._native) != _stable(snapshot._native):
        raise GatewayConflictError
    return current


def _desired(before: GatewaySnapshot, settings: GatewaySettings) -> tuple[bytes, dict[int, int]]:
    value = bytearray(before._native)
    masks: dict[int, int] = {}

    def set_byte(offset: int, new: int, mask: int = 255) -> None:
        masks[offset] = masks.get(offset, 0) | mask
        value[offset] = (value[offset] & ~mask) | (new & mask)

    # Explicit adapter setup establishes one passive request/reply endpoint.
    # VirCom's Simple Modbus TCP to RTU selection serializes type=0 (0x37)
    # and query engine=0 (0x71), independently of conversion selector 0x3C.
    # These also disable the engine when the selected transport stays RTU.
    set_byte(0x14, 0)  # Documented TCP-server mode; keep address/DHCP untouched.
    set_byte(0x37, 0)
    set_byte(0x71, 0)
    set_byte(0x39, 0)  # No RTS/CTS or software flow control on this RS485 link.
    set_byte(0x6E, 0, _AUTONOMOUS_FLAGS)

    if settings.baudrate is not None:
        set_byte(0x25, _BAUDRATES.index(settings.baudrate))
    if settings.frame is not None:
        set_byte(0x30, _PARITY.index(settings.frame[1].upper()))
        set_byte(0x3B, 8 - int(settings.frame[0]))
        set_byte(0x72, int(settings.frame[2]) - 1, 1)
    if settings.connection_type is not None:
        set_byte(0x3C, int(settings.connection_type == "modbus_tcp"))
    if settings.tcp_authentication is not None:
        set_byte(0x72, int(settings.tcp_authentication) * 2, 2)
    if settings.tcp_port is not None:
        if settings.tcp_port == int.from_bytes(value[0x62:0x64], "big"):
            raise ValueError("Adapter TCP and web ports must differ")
        set_byte(0x10, settings.tcp_port >> 8)
        set_byte(0x11, settings.tcp_port & 255)
    # Own only fields that actually change. An unchanged selection must not
    # prevent restoring another setting after an unrelated concurrent edit.
    masks = {offset: (mask & (before._native[offset] ^ value[offset]) if offset in _BIT_FIELDS else mask)
             for offset, mask in masks.items() if (before._native[offset] ^ value[offset]) & mask}
    return bytes(value), masks


async def _verify_auth(snapshot: GatewaySnapshot, password: str) -> None:
    if snapshot.tcp_authentication:
        validate_tcp_auth_password(password)
        try:
            sock = await async_open_authenticated_socket(snapshot.address, snapshot.tcp_port, password, timeout_seconds=_AUTH_TIMEOUT)
        except WaveshareAuthenticationRejectedError:
            raise GatewayError("gateway_authentication_failed") from None
        except WaveshareAuthenticationError:
            raise GatewayError("gateway_unreachable") from None
        sock.close()


async def _send_parameters(snapshot: GatewaySnapshot, native: bytes) -> None:
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(_QUERY_TIMEOUT):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            await loop.sock_connect(sock, (snapshot.address, _MANAGEMENT_PORT))
            await loop.sock_sendall(sock, b"ZL\x02" + native)


async def _wait_readback(before: GatewaySnapshot, native: bytes) -> GatewaySnapshot:
    try:
        async with asyncio.timeout(_APPLY_TIMEOUT):
            while True:
                await asyncio.sleep(0.5)
                try:
                    after = await _query_native(before.address)
                except GatewayUnsupportedError:
                    raise
                except GatewayError:
                    continue
                _assert_identity(before, after)
                if _stable(after._native) == _stable(native):
                    return replace(after, model=before.model)
    except TimeoutError:
        raise GatewayError("gateway_apply_failed") from None


async def _wait_available(before: GatewaySnapshot) -> GatewaySnapshot:
    """A failed apply can leave the adapter temporarily rebooting."""
    async with asyncio.timeout(_APPLY_TIMEOUT):
        while True:
            try:
                current = await _query_native(before.address)
            except GatewayUnsupportedError:
                raise
            except GatewayError:
                await asyncio.sleep(0.5)
                continue
            _assert_identity(before, current)
            return replace(current, model=before.model)


class GatewayReceipt:
    """An uncommitted change is restored on context exit, even after cancellation."""

    def __init__(self, before: GatewaySnapshot, after: GatewaySnapshot, masks: dict[int, int], password: str, lock: asyncio.Lock) -> None:
        self.before, self.after = before, after
        self._masks, self._password, self._lock = masks, password, lock
        self._attempted = False
        self._apply_confirmed = False
        self._finished = False
        self.rollback_verified = False
        self._completion_lock = asyncio.Lock()
        self._rollback_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if not self._finished:
            await self.rollback()

    def _release(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._password = ""
        self._lock.release()

    async def commit(self) -> None:
        """Commit only after the caller has validated and saved its connection."""
        async with self._completion_lock:
            if self._finished:
                return
            current = await _query_native(self.before.address)
            _assert_identity(self.before, current)
            if _stable(current._native) != _stable(self.after._native):
                raise GatewayConflictError
            self._release()

    async def rollback(self) -> None:
        """Complete one bounded restoration task independently of cancellation."""
        if self._finished:
            return
        if self._rollback_task is None:
            self._rollback_task = asyncio.create_task(self._restore(), name="amtron_adapter_restore")
        cancelled = False
        while not self._rollback_task.done():
            try:
                await asyncio.shield(self._rollback_task)
            except asyncio.CancelledError:
                cancelled = True
        # Do not report cancellation alone if rollback itself failed.
        self._rollback_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _restore(self) -> None:
        async with self._completion_lock:
            if self._finished:
                return
            try:
                async with asyncio.timeout(_ROLLBACK_TIMEOUT):
                    if self._attempted:
                        current = await _wait_available(self.before)
                        restore = bytearray(current._native)
                        for offset, mask in self._masks.items():
                            old, intended, now = (data[offset] & mask for data in (self.before._native, self.after._native, current._native))
                            # Byte-wide values cannot be safely merged with a
                            # third party's conflicting edit. Independent flag
                            # bits may have been only partially applied.
                            if offset not in _BIT_FIELDS and now not in (old, intended):
                                raise GatewayConflictError
                            restore[offset] = (restore[offset] & ~mask) | old
                        if _stable(bytes(restore)) != _stable(current._native):
                            await _send_parameters(current, bytes(restore))
                            current = await _wait_readback(current, bytes(restore))
                        await _verify_auth(current, self._password)
                        if not self._apply_confirmed:
                            # UDP has no delivery acknowledgement. A readback of
                            # the old settings cannot rule out the original write
                            # arriving after this best-effort restoration.
                            raise GatewayRollbackError
                    self.rollback_verified = True
            except Exception:  # noqa: BLE001 - Every failed restoration must prevent reconnect.
                raise GatewayRollbackError from None
            finally:
                self._release()


async def configure_gateway(
    host: str, settings: GatewaySettings, password: str = "", *,
    expected: GatewaySnapshot | None = None,
) -> GatewayReceipt:
    """Apply the passive profile; caller commits after its successful validation/save."""
    address = await _resolve_address(host)
    locks = _LOCKS.setdefault(asyncio.get_running_loop(), WeakValueDictionary())
    lock = locks.setdefault(address, asyncio.Lock())
    await lock.acquire()
    receipt: GatewayReceipt | None = None
    try:
        before = await inspect_gateway(address)
        if expected is not None:
            _assert_identity(expected, before)
            if _stable(expected._native) != _stable(before._native):
                raise GatewayConflictError
        if not before.can_configure:
            raise GatewayUnsupportedError
        native, masks = _desired(before, settings)
        after = _snapshot(address, native)
        if before.tcp_authentication or after.tcp_authentication:
            validate_tcp_auth_password(password)
        await _verify_auth(before, password)
        current = replace(await _query_native(address), model=before.model)
        _assert_identity(before, current)
        if _stable(current._native) != _stable(before._native):
            raise GatewayConflictError
        # Preserve the freshest volatile bytes too; only requested fields differ.
        native, masks = _desired(current, settings)
        receipt = GatewayReceipt(current, replace(_snapshot(address, native), model=before.model), masks, password, lock)
        if _stable(native) != _stable(current._native):
            receipt._attempted = True
            await _send_parameters(current, native)
            receipt.after = await _wait_readback(current, native)
            receipt._apply_confirmed = True
        await _verify_auth(receipt.after, password)
        return receipt
    except BaseException:
        if receipt is not None:
            await receipt.rollback()
        else:
            lock.release()
        raise
