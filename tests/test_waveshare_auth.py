"""Vendor login over local sockets, with an independent bounded fake gateway."""

from __future__ import annotations

import asyncio
import logging
import socket
from contextlib import asynccontextmanager, suppress

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from custom_components.mennekes_amtron_rtu.device import waveshare_auth as auth
from custom_components.mennekes_amtron_rtu.device.client import (
    MennekesModbusClient,
    create_modbus_client,
)
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.connection import NetworkConfig
from custom_components.mennekes_amtron_rtu.device.registers import EVSE_STATE, HEARTBEAT
from custom_components.mennekes_amtron_rtu.device.waveshare_transport import (
    WaveshareModbusTcpClient,
)
from tests.fakes import device_bank
from tests.test_network_wire import PROTOCOLS, register_reply
from tests.test_serial_faults import frame

PASSWORD = "Test-7key"  # noqa: S105 - Synthetic fixture, unrelated to a real device.
PREFIX = bytes.fromhex("5a4c284e")


def device_response(challenge):
    cipher = Cipher(algorithms.AES(PASSWORD.encode().ljust(16, b"\0")), modes.ECB())  # noqa: S305
    decryptor = cipher.decryptor()
    return decryptor.update(challenge) + decryptor.finalize()


class Gateway:
    """Receive real TCP bytes; never call the production authentication codec."""

    def __init__(self, *, protocol=None, failure=None, fragment=37, delay=0):
        self.protocol = protocol
        self.failure = failure
        self.fragment = fragment
        self.delay = delay
        self.writers = []
        self.tasks = []
        self.handshakes = []
        self.application = []
        self.accepted = 0
        self.first_sent = asyncio.Event()
        self.final_gate = None
        self.client_closed = asyncio.Event()
        self.bank = device_bank()

    async def emit(self, writer, packet):
        command = packet[4]
        if self.failure == f"short{command}":
            writer.write(packet[:15])
            await writer.drain()
            writer.close()
            return
        for kind, offset in (("prefix", 0), ("command", 4), ("session", 5), ("device", 7), ("length", 19)):
            if self.failure == f"{kind}{command}":
                packet = packet[:offset] + bytes([packet[offset] ^ 1]) + packet[offset + 1:]
        if self.failure == f"response{command}":
            packet = packet[:21] + b"\x02" * 16
        if self.failure == "reject" and command == 5:
            packet = packet[:21] + bytes(16)
        for offset in range(0, len(packet), self.fragment):
            writer.write(packet[offset:offset + self.fragment])
            await writer.drain()
            await asyncio.sleep(0)

    async def handle(self, reader, writer):
        self.writers.append(writer)
        received = []
        self.handshakes.append(received)
        identity = len(self.writers).to_bytes(2, "little") + b"TESTDEVICE01" + b"\x10\x00"
        challenge = bytes([len(self.writers)]) * 16
        try:
            await self.emit(writer, PREFIX + b"\x01" + identity + challenge)
            self.first_sent.set()
            second = await reader.readexactly(37)
            received.append(second)
            assert second[:21] == PREFIX + b"\x02" + identity
            if self.failure == "stall":
                assert await reader.read() == b""
                return
            if self.delay:
                await asyncio.sleep(self.delay)
            await self.emit(writer, PREFIX + b"\x03" + identity + device_response(second[21:]))
            fourth = await reader.readexactly(37)
            received.append(fourth)
            assert fourth[:21] == PREFIX + b"\x04" + identity
            assert fourth[21:] == device_response(challenge)
            if self.final_gate is not None:
                await self.final_gate.wait()
            await self.emit(writer, PREFIX + b"\x05" + identity + b"\x01" * 16)
            if self.failure:
                assert await reader.read() == b""
                return
            self.accepted += 1
            if self.protocol is None:
                assert await reader.read() == b""
                return
            while True:
                if self.protocol == "modbus_tcp":
                    header = await reader.readexactly(7)
                    size = int.from_bytes(header[4:6], "big")
                    payload = header[6:] + await reader.readexactly(size - 1)
                else:
                    payload = await reader.readexactly(6)
                    assert await reader.readexactly(2) == frame(payload)[-2:]
                self.application.append(payload)
                response = register_reply(payload, self.bank)
                wire = header[:4] + len(response).to_bytes(2, "big") + response if self.protocol == "modbus_tcp" else frame(response)
                writer.write(wire)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.client_closed.set()
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()

    def accept(self, reader, writer):
        self.tasks.append(asyncio.create_task(self.handle(reader, writer)))


@asynccontextmanager
async def gateway_peer(**kwargs):
    gateway = Gateway(**kwargs)
    server = await asyncio.start_server(gateway.accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], gateway
    finally:
        server.close()
        if gateway.final_gate is not None:
            gateway.final_gate.set()
        for writer in gateway.writers:
            writer.close()
        await asyncio.gather(*gateway.tasks)
        await server.wait_closed()


@pytest.mark.parametrize("fragment", [1, 2, 7, 36, 37])
def test_authentication_handles_fragmented_frames_without_application_bytes(fragment):
    async def run():
        async with gateway_peer(fragment=fragment) as (port, gateway):
            sock = await auth.async_open_authenticated_socket("127.0.0.1", port, PASSWORD)
            assert not sock.getblocking()
            sock.close()
            await gateway.client_closed.wait()
            assert gateway.accepted == 1
            assert gateway.application == []
            assert [packet[4] for packet in gateway.handshakes[0]] == [2, 4]

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("failure", [
    "prefix1", "command1", "length1", "short1", "prefix3", "command3",
    "session3", "device3", "length3", "short3", "response3", "prefix5",
    "command5", "session5", "device5", "length5", "short5", "response5", "reject",
])
def test_invalid_frame_or_response_closes_socket_without_fallback(failure):
    async def run():
        async with gateway_peer(failure=failure) as (port, gateway):
            with pytest.raises(auth.WaveshareAuthenticationError) as error:
                await auth.async_open_authenticated_socket("127.0.0.1", port, PASSWORD)
            assert PASSWORD not in str(error.value)
            assert isinstance(error.value, auth.WaveshareAuthenticationRejectedError) == (failure in ("response3", "reject"))
            await gateway.client_closed.wait()
            assert gateway.accepted == 0
            assert gateway.application == []
            if failure.endswith("3"):
                assert [packet[4] for packet in gateway.handshakes[0]] == [2]

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancellation_close_raw_socket(cancel):
    async def run():
        async with gateway_peer(failure="stall") as (port, gateway):
            task = asyncio.create_task(auth.async_open_authenticated_socket(
                "127.0.0.1", port, PASSWORD, timeout_seconds=0.05,
            ))
            await gateway.first_sent.wait()
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else auth.WaveshareAuthenticationError):
                await task
            await gateway.client_closed.wait()
            assert gateway.application == []

    asyncio.run(asyncio.wait_for(run(), 3))


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), 11])
def test_invalid_timeout_fails_before_network(timeout):
    async def run():
        with pytest.raises(ValueError, match="timeout"):
            await auth.async_open_authenticated_socket("127.0.0.1", 1, PASSWORD, timeout)

    asyncio.run(run())


def test_invalid_password_closes_caller_owned_socket_without_sending():
    async def run():
        sock, other = socket.socketpair()
        sock.setblocking(False)
        try:
            with pytest.raises(ValueError, match="password"):
                await auth.async_authenticate_socket(sock, "")
            assert sock.fileno() == -1
        finally:
            sock.close()
            other.close()

    asyncio.run(run())


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_pymodbus_authenticates_before_connect_and_preserves_both_framers(protocol, caplog):
    caplog.set_level(logging.DEBUG, logger="pymodbus.logging")

    async def run():
        packet_trace = []
        connection_trace = []
        async with gateway_peer(protocol=protocol, fragment=2) as (port, gateway):
            config = NetworkConfig("127.0.0.1", tcp_port=port, connection_type=protocol,
                                   tcp_authentication=True, tcp_auth_password=PASSWORD)
            client = create_modbus_client(
                config, trace_packet=lambda sending, data: packet_trace.append((sending, data)) or data,
                trace_connect=connection_trace.append,
            )
            assert isinstance(client, WaveshareModbusTcpClient)
            gateway.final_gate = asyncio.Event()
            task = asyncio.create_task(client.connect())
            await gateway.first_sent.wait()
            await asyncio.sleep(0.01)
            assert not client.connected
            assert packet_trace == connection_trace == []
            gateway.final_gate.set()
            try:
                assert await task
                assert await client.connect()  # An open connection is not logged in twice.
                result = await client.read_holding_registers(EVSE_STATE.address, count=1, device_id=50)
                assert result.registers == [1]
                assert len(gateway.writers) == 1
                assert connection_trace == [True]
                assert all(PREFIX not in packet for _, packet in packet_trace)
                assert all(PREFIX.hex() not in message.replace(" ", "") for message in caplog.messages)
                assert PASSWORD not in caplog.text
            finally:
                client.close()
            await gateway.client_closed.wait()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("failure", ["timeout", "reset", "blocking"])
def test_caller_owned_socket_is_closed_on_receive_errors(failure, monkeypatch):
    async def run():
        sock, other = socket.socketpair()
        sock.setblocking(failure == "blocking")
        try:
            if failure == "reset":
                async def reset(*args):
                    raise ConnectionResetError("sensitive transport details")

                monkeypatch.setattr(asyncio.get_running_loop(), "sock_recv", reset)
            expected = ValueError if failure == "blocking" else auth.WaveshareAuthenticationError
            with pytest.raises(expected, match="authentication") as error:
                await auth.async_authenticate_socket(sock, PASSWORD, timeout_seconds=0.005)
            assert "sensitive" not in str(error.value)
            assert not isinstance(error.value, auth.WaveshareAuthenticationRejectedError)
            assert sock.fileno() == -1
        finally:
            sock.close()
            other.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["dns", "no_addresses", "refused"])
def test_open_errors_are_bounded_and_sanitized(failure, monkeypatch):
    async def run():
        async def addresses(*args, **kwargs):
            if failure == "dns":
                raise OSError("sensitive resolver details")
            if failure == "no_addresses":
                return []
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 1))]

        sockets = []

        async def refuse(sock, address):
            sockets.append(sock)
            raise ConnectionRefusedError("sensitive connection details")

        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", addresses)
        monkeypatch.setattr(loop, "sock_connect", refuse)
        with pytest.raises(auth.WaveshareAuthenticationError) as error:
            await auth.async_open_authenticated_socket("127.0.0.1", 1, PASSWORD)
        assert "sensitive" not in str(error.value)
        assert all(sock.fileno() == -1 for sock in sockets)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["attach_error", "not_consumed", "closed_before_attach"])
def test_authenticated_socket_never_leaks_when_pymodbus_attachment_fails(failure, monkeypatch):
    async def run():
        async with gateway_peer() as (port, gateway):
            client = WaveshareModbusTcpClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, tcp_authentication=True,
                tcp_auth_password=PASSWORD,
            ))
            attachment_started = asyncio.Event()

            async def connect_failed():
                attachment_started.set()
                if failure == "closed_before_attach":
                    await asyncio.Event().wait()
                return False

            async def attach_failed(*args, **kwargs):
                raise OSError("fixture cannot attach authenticated socket")

            if failure == "attach_error":
                monkeypatch.setattr(asyncio.get_running_loop(), "create_connection", attach_failed)
            else:
                monkeypatch.setattr(client.ctx, "connect", connect_failed)
            task = asyncio.create_task(client.connect())
            if failure == "closed_before_attach":
                await attachment_started.wait()
                client.close()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert not await task
            assert not client.connected
            await gateway.client_closed.wait()
            assert gateway.application == []
            client.close()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_reconnect_authenticates_again_before_next_modbus_operation(protocol):
    async def run():
        async with gateway_peer(protocol=protocol) as (port, gateway):
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type=protocol,
                tcp_authentication=True, tcp_auth_password=PASSWORD,
            ))
            try:
                await client.async_connect()
                assert await client.async_read_register(EVSE_STATE) == 1
                disconnected = asyncio.Event()
                client._client.ctx.trace_connect = lambda connected: None if connected else disconnected.set()
                gateway.writers[0].close()
                await disconnected.wait()
                await client.async_write_register(HEARTBEAT, 0x55AA)
                assert gateway.accepted == 2
                assert len(gateway.application) == 2
                assert gateway.handshakes[0][0][21:] != gateway.handshakes[1][0][21:]
                assert client._client.ctx.comm_params.reconnect_delay == 0
                assert client._client.ctx.comm_params.timeout_connect == client.config.timeout
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_internal_pymodbus_reconnect_hook_cannot_skip_authentication():
    async def run():
        async with gateway_peer(protocol="modbus_tcp") as (port, gateway):
            client = WaveshareModbusTcpClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, connection_type="modbus_tcp",
                tcp_authentication=True, tcp_auth_password=PASSWORD,
            ))
            try:
                assert await client.ctx.connect()
                assert gateway.accepted == 1
                response = await client.read_holding_registers(EVSE_STATE.address, count=1, device_id=50)
                assert response.registers == [1]
            finally:
                client.close()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("close", [False, True])
@pytest.mark.parametrize("internal", [False, True])
def test_cancel_or_close_while_authenticating_never_marks_client_connected(close, internal):
    async def run():
        async with gateway_peer(failure="stall") as (port, gateway):
            connection_trace = []
            client = WaveshareModbusTcpClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, tcp_authentication=True,
                tcp_auth_password=PASSWORD,
            ), trace_connect=connection_trace.append)
            task = asyncio.create_task(client.ctx.connect() if internal else client.connect())
            await gateway.first_sent.wait()
            if close:
                client.close()
            else:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not client.connected
            assert connection_trace == []
            await gateway.client_closed.wait()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_authentication_budget_does_not_extend_modbus_response_timeout():
    async def run():
        async with gateway_peer(delay=0.08) as (port, gateway):
            client = WaveshareModbusTcpClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, timeout=0.02,
                tcp_authentication=True, tcp_auth_password=PASSWORD,
            ))
            try:
                assert await client.connect()
                assert gateway.accepted == 1
                assert client.ctx.comm_params.timeout_connect == 0.02
            finally:
                client.close()

    asyncio.run(asyncio.wait_for(run(), 5))


def test_wrong_password_exposes_only_sanitized_connection_error():
    async def run():
        async with gateway_peer() as (port, gateway):
            wrong_key = PASSWORD[::-1]
            client = MennekesModbusClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, tcp_authentication=True,
                tcp_auth_password=wrong_key,
            ))
            try:
                with pytest.raises(AmtronConnectionError, match="authentication failed") as error:
                    await client.async_connect()
                assert isinstance(error.value, auth.WaveshareAuthenticationRejectedError)
                assert wrong_key not in str(error.value)
                assert not client.connected
                assert len(gateway.handshakes[0]) == 1
            finally:
                await client.async_close()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("failure", ["response3", "reject"])
def test_definitive_rejection_blocks_fresh_sockets_until_new_client(internal, failure):
    async def run():
        async with gateway_peer(failure=failure) as (port, gateway):
            config = NetworkConfig(
                "127.0.0.1", tcp_port=port, tcp_authentication=True,
                tcp_auth_password=PASSWORD,
            )
            client = WaveshareModbusTcpClient(config)
            try:
                with pytest.raises(auth.WaveshareAuthenticationRejectedError) as first:
                    await (client.ctx.connect() if internal else client.connect())
                await gateway.client_closed.wait()
                gateway.failure = None
                client.close()  # Closing must not clear a definitive rejection.
                for connect in (client.connect, client.ctx.connect, client.connect):
                    with pytest.raises(auth.WaveshareAuthenticationRejectedError) as retry:
                        await connect()
                    assert retry.value is not first.value
                    assert PASSWORD not in str(retry.value)
                    assert not client.connected
                assert len(gateway.handshakes) == 1
                assert gateway.application == []
            finally:
                client.close()
            replacement = WaveshareModbusTcpClient(config)
            try:
                assert await replacement.connect()
                assert len(gateway.handshakes) == 2
                assert gateway.accepted == 1
            finally:
                replacement.close()

    asyncio.run(asyncio.wait_for(run(), 5))


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("failure", ["prefix3", "response5", "stall"])
def test_transient_login_failures_remain_retryable(internal, failure, monkeypatch):
    monkeypatch.setattr(auth, "_STEP_TIMEOUT", 0.03)

    async def run():
        async with gateway_peer(failure=failure) as (port, gateway):
            client = WaveshareModbusTcpClient(NetworkConfig(
                "127.0.0.1", tcp_port=port, tcp_authentication=True,
                tcp_auth_password=PASSWORD,
            ))
            try:
                with pytest.raises(auth.WaveshareAuthenticationError) as error:
                    await (client.ctx.connect() if internal else client.connect())
                assert not isinstance(error.value, auth.WaveshareAuthenticationRejectedError)
                await gateway.client_closed.wait()
                gateway.failure = None
                assert await client.connect()
                assert len(gateway.handshakes) == 2
                assert gateway.accepted == 1
            finally:
                client.close()

    asyncio.run(asyncio.wait_for(run(), 5))
