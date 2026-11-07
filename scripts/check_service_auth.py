"""Exercise installed TCP authentication with an isolated loopback gateway.

Image smoke runs this inside the service's AppArmor profile on each native
architecture. It requires neither Supervisor nor physical serial hardware.
"""

from __future__ import annotations

import asyncio
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


async def check_authentication() -> None:
    """Require the packaged helper to complete all five wire messages."""
    from amtron_device.waveshare_auth import async_open_authenticated_socket

    password = secrets.token_hex(4)
    prefix = bytes.fromhex("5a4c284e")
    identity = b"\x01\x00" + b"SMOKETEST001" + b"\x10\x00"
    challenge = bytes(range(16))
    tasks: list[asyncio.Task[None]] = []
    writers: list[asyncio.StreamWriter] = []

    def response(value: bytes) -> bytes:
        # Match the vendor's fixed single-block calculation, not transport TLS.
        cipher = Cipher(algorithms.AES(password.encode().ljust(16, b"\0")), modes.ECB())  # noqa: S305
        decryptor = cipher.decryptor()
        return decryptor.update(value) + decryptor.finalize()

    async def peer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        try:
            writer.write(prefix + b"\x01" + identity + challenge)
            await writer.drain()
            second = await reader.readexactly(37)
            if second[:21] != prefix + b"\x02" + identity:
                raise ValueError("installed authentication sent an unexpected challenge frame")
            third = prefix + b"\x03" + identity + response(second[21:])
            # Fragment a frame to exercise the real fixed-length receiver.
            writer.write(third[:9])
            await writer.drain()
            await asyncio.sleep(0)
            writer.write(third[9:])
            await writer.drain()
            fourth = await reader.readexactly(37)
            if fourth != prefix + b"\x04" + identity + response(challenge):
                raise ValueError("installed authentication sent an invalid password proof")
            writer.write(prefix + b"\x05" + identity + b"\x01" * 16)
            await writer.drain()
            if await reader.read() != b"":
                raise ValueError("authentication-only probe sent application data")
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks.append(asyncio.create_task(peer(reader, writer)))

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        async with asyncio.timeout(5):
            sock = await async_open_authenticated_socket(
                "127.0.0.1", server.sockets[0].getsockname()[1], password,
                timeout_seconds=3,
            )
            sock.close()
            await asyncio.gather(*tasks)
    finally:
        server.close()
        for writer in writers:
            writer.close()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(check_authentication())
