"""Authenticate a socket before handing it to the pinned pymodbus transport."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from typing import cast

from pymodbus import FramerType
from pymodbus.client import AsyncModbusTcpClient

from .connection import NetworkConfig
from .waveshare_auth import (
    WaveshareAuthenticationRejectedError,
    async_open_authenticated_socket,
)


class WaveshareModbusTcpClient(AsyncModbusTcpClient):
    """Keep the vendor login outside pymodbus framing, tracing and reply timers.

    Only the connection-creation hook of the pinned pymodbus version is used.
    Public connects prepare the authenticated socket before pymodbus's shorter
    connection timer starts. Its internal reconnect path uses the same hook,
    so it cannot bypass login even if the link drops just before a transaction.
    """

    def __init__(
        self, config: NetworkConfig, *,
        trace_packet: Callable[[bool, bytes], bytes] | None = None,
        trace_connect: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(
            config.host, port=config.tcp_port,
            framer=FramerType.RTU if config.connection_type == "rtu_over_tcp" else FramerType.SOCKET,
            timeout=config.timeout, retries=0, reconnect_delay=0,
            trace_packet=trace_packet, trace_connect=trace_connect,
        )
        self._password = config.tcp_auth_password
        self._authentication_rejected = False
        self._prepared_socket: socket.socket | None = None
        self._connecting_task: asyncio.Task[bool] | None = None
        self._creating_task: asyncio.Task[tuple[asyncio.Transport, asyncio.Protocol]] | None = None
        self._connect_lock = asyncio.Lock()
        self.ctx.call_create = self._create_authenticated_connection

    async def _open_authenticated_socket(self) -> socket.socket:
        """Retry transport faults, but require new configuration after rejection.

        Keep only a flag, never a stored exception/traceback with credentials.
        A new client after reauthentication or an app restart clears the flag.
        """
        if self._authentication_rejected:
            raise WaveshareAuthenticationRejectedError("TCP gateway authentication requires new credentials")
        try:
            return await async_open_authenticated_socket(
                self.comm_params.host, self.comm_params.port, self._password,
            )
        except WaveshareAuthenticationRejectedError:
            self._authentication_rejected = True
            raise

    async def _create_authenticated_connection(
        self,
    ) -> tuple[asyncio.Transport, asyncio.Protocol]:
        self._creating_task = asyncio.current_task()
        sock, self._prepared_socket = self._prepared_socket, None
        try:
            if sock is None:
                # A rare library-internal reconnect is still authenticated; its
                # existing connect timeout remains an additional upper bound.
                sock = await self._open_authenticated_socket()
            # pymodbus's Protocol implementation is annotated as BaseProtocol.
            protocol_factory = cast(Callable[[], asyncio.Protocol], self.ctx.handle_new_connection)
            return await asyncio.get_running_loop().create_connection(
                protocol_factory, sock=sock,
            )
        except BaseException:
            if sock is not None:
                sock.close()
            self.ctx.close()
            raise
        finally:
            self._creating_task = None

    async def connect(self) -> bool:
        """A new connection receives a separate bounded authentication phase."""
        async with self._connect_lock:
            if self.connected:
                return True
            task = asyncio.current_task()
            self._connecting_task = task
            try:
                self._prepared_socket = await self._open_authenticated_socket()
                return await super().connect()
            except BaseException:
                super().close()
                raise
            finally:
                self._connecting_task = None
                if self._prepared_socket is not None:
                    self._prepared_socket.close()
                    self._prepared_socket = None

    def close(self) -> None:
        """Closing during login cancels it instead of opening a late socket."""
        if self._connecting_task is not None:
            self._connecting_task.cancel()
        if self._creating_task is not None:
            self._creating_task.cancel()
        if self._prepared_socket is not None:
            self._prepared_socket.close()
            self._prepared_socket = None
        super().close()
