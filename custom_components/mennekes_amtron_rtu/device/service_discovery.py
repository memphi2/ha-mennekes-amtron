"""Private Supervisor discovery, independent of Modbus and Home Assistant Core."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import DOMAIN
from .service_config import ServerConfig, validate_token

_LOGGER = logging.getLogger(__name__)
SUPERVISOR_URL = "http://supervisor"
RETRY_SECONDS = 30
APP_HOSTNAME = re.compile(r"(?:[a-f0-9]{8}|local)-mennekes-amtron-modbus")


def app_token(path: Path) -> str:
    """Create one private persistent token; never rotate it on app restarts."""
    try:
        return validate_token(path.read_text())
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    descriptor, name = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return token


async def _request(
    session: ClientSession, method: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    async with session.request(
        method, SUPERVISOR_URL + path, allow_redirects=False, **kwargs
    ) as response:
        if response.status != 200:
            raise ValueError("Supervisor discovery request failed")
        # Never log API bodies: self info and discovery contain credentials.
        payload = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            payload.extend(chunk)
            if len(payload) > 65536:
                raise ValueError("Supervisor response is too large")
        result = json.loads(payload)
        if not isinstance(result, dict) or result.get("result") != "ok":
            raise ValueError("invalid Supervisor response")
        data = result.get("data")
        if not isinstance(data, dict):
            raise ValueError("invalid Supervisor response data")
        return data


async def publish_discovery(config: ServerConfig, supervisor_token: str) -> None:
    """Register once per start; Supervisor retains discovery across Core restarts."""
    async with ClientSession(
        headers={"Authorization": f"Bearer {supervisor_token}"},
        timeout=ClientTimeout(total=10),
        trust_env=False,
    ) as session:
        warned = False
        while True:
            try:
                info = await _request(session, "GET", "/addons/self/info")
                hostname = info.get("hostname")
                if not isinstance(hostname, str) or not APP_HOSTNAME.fullmatch(
                    hostname
                ):
                    raise ValueError("unexpected app hostname")
                await _request(
                    session,
                    "POST",
                    "/discovery",
                    json={
                        "service": DOMAIN,
                        "config": {
                            "host": hostname,
                            "port": config.port,
                            "token": config.token,
                        },
                    },
                )
            except ClientError, TimeoutError, ValueError:
                if not warned:
                    _LOGGER.warning(
                        "Supervisor discovery unavailable; retrying in the background"
                    )
                    warned = True
                await asyncio.sleep(RETRY_SECONDS)
            else:
                _LOGGER.info("Modbus service registered with Home Assistant discovery")
                return
