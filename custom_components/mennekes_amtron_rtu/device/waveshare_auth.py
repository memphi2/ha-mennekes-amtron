"""Wired Waveshare TCP login, isolated from Modbus and its packet logging.

This vendor compatibility protocol does not encrypt application traffic. Its
reflection weakness means a successful handshake is not a secure transport.
No gateway settings are changed here; each new TCP socket authenticates anew.
"""

from __future__ import annotations

import asyncio
import hmac
import math
import secrets
import socket

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .client_errors import AmtronConnectionError
from .connection import (
    validate_network_host,
    validate_tcp_auth_password,
    validate_tcp_port,
)

AUTHENTICATION_TIMEOUT = 10.0
_STEP_TIMEOUT = 3.0
_PREFIX = b"\x5a\x4c\x28\x4e"
_FRAME_SIZE = 37


class WaveshareAuthenticationError(AmtronConnectionError):
    """The gateway login failed; messages never include credentials or frames."""


class WaveshareAuthenticationRejectedError(WaveshareAuthenticationError):
    """A complete password proof failed, rather than a transient network error."""


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool) or not math.isfinite(timeout)
        or not 0 < timeout <= AUTHENTICATION_TIMEOUT
    ):
        raise ValueError("authentication timeout must be positive and at most ten seconds")


def _response(challenge: bytes, password: str) -> bytes:
    """The vendor uses AES decryption, not encryption, for both challenges."""
    key = password.encode("ascii").ljust(16, b"\x00")[:16]
    # The fixed single-block vendor protocol mandates ECB; this is not TLS.
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # noqa: S305
    return decryptor.update(challenge) + decryptor.finalize()


async def _receive(
    sock: socket.socket, command: int, identity: bytes | None = None,
) -> bytes:
    """Read precisely one fixed frame without consuming later Modbus bytes."""
    loop = asyncio.get_running_loop()
    data = bytearray()
    async with asyncio.timeout(_STEP_TIMEOUT):
        while len(data) < _FRAME_SIZE:
            chunk = await loop.sock_recv(sock, _FRAME_SIZE - len(data))
            if not chunk:
                raise WaveshareAuthenticationError("TCP gateway closed during authentication")
            data.extend(chunk)
    packet = bytes(data)
    if (
        packet[:4] != _PREFIX
        or packet[4] != command
        or packet[19:21] != b"\x10\x00"
        or (identity is not None and packet[5:21] != identity)
    ):
        raise WaveshareAuthenticationError("TCP gateway sent an invalid authentication frame")
    return packet


async def async_authenticate_socket(
    sock: socket.socket, password: str, timeout_seconds: float = AUTHENTICATION_TIMEOUT,
) -> None:
    """Authenticate one connected nonblocking socket; close it on any failure.

    The caller retains ownership on success. No payload reaches pymodbus or
    trace callbacks, and no application data is sent before the final result.
    """
    try:
        validate_tcp_auth_password(password)
        _validate_timeout(timeout_seconds)
        if sock.getblocking():
            raise ValueError("authentication socket must be nonblocking")
        async with asyncio.timeout(timeout_seconds):
            first = await _receive(sock, 1)
            identity = first[5:21]
            peer_challenge = secrets.token_bytes(16)
            loop = asyncio.get_running_loop()
            await loop.sock_sendall(sock, _PREFIX + b"\x02" + identity + peer_challenge)
            third = await _receive(sock, 3, identity)
            if not hmac.compare_digest(third[21:], _response(peer_challenge, password)):
                raise WaveshareAuthenticationRejectedError("TCP gateway authentication failed")
            await loop.sock_sendall(
                sock, _PREFIX + b"\x04" + identity + _response(first[21:], password),
            )
            fifth = await _receive(sock, 5, identity)
            if fifth[21:] == bytes(16):
                raise WaveshareAuthenticationRejectedError("TCP gateway rejected authentication")
            if fifth[21:] != b"\x01" * 16:
                raise WaveshareAuthenticationError("TCP gateway sent an invalid authentication result")
    except TimeoutError:
        sock.close()
        raise WaveshareAuthenticationError("TCP gateway authentication timed out") from None
    except OSError:
        sock.close()
        raise WaveshareAuthenticationError("TCP gateway authentication connection failed") from None
    except BaseException:
        sock.close()
        raise


async def async_open_authenticated_socket(
    host: str, port: int, password: str, timeout_seconds: float = AUTHENTICATION_TIMEOUT,
) -> socket.socket:
    """Open and authenticate TCP, returning the same caller-owned socket.

    Address resolution, connection attempts and login share one deadline.
    A rejected login never falls back to an unauthenticated connection.
    """
    host = validate_network_host(host)
    validate_tcp_port(port)
    validate_tcp_auth_password(password)
    _validate_timeout(timeout_seconds)
    loop = asyncio.get_running_loop()
    sock: socket.socket | None = None
    authenticated = False
    try:
        async with asyncio.timeout(timeout_seconds):
            addresses = await loop.getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
            )
            for family, kind, protocol, _name, address in addresses:
                sock = socket.socket(family, kind, protocol)
                sock.setblocking(False)
                try:
                    async with asyncio.timeout(_STEP_TIMEOUT):
                        await loop.sock_connect(sock, address)
                except (OSError, TimeoutError):
                    sock.close()
                    sock = None
                    continue
                await async_authenticate_socket(sock, password, timeout_seconds)
                authenticated = True
                return sock
            raise WaveshareAuthenticationError("Cannot connect to TCP authentication gateway")
    except TimeoutError:
        raise WaveshareAuthenticationError("TCP gateway authentication timed out") from None
    except OSError:
        raise WaveshareAuthenticationError("TCP gateway authentication connection failed") from None
    finally:
        # A returned socket remains open; every exception/cancellation closes it.
        if sock is not None and not authenticated:
            sock.close()
