"""Short-lived, private adapter identities for explicit ingress setup requests."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .connection import validate_network_host
from .waveshare_gateway import GatewaySnapshot, inspect_gateway

_PROBE_LIFETIME_SECONDS = 300
_MAX_PROBES = 4


@dataclass(frozen=True, slots=True)
class _Probe:
    user: str
    host: str
    revision: str
    expires: float
    snapshot: GatewaySnapshot = field(repr=False)


class AdapterSetup:
    """Bind detected hardware to the administrator and unchanged app settings."""

    def __init__(self) -> None:
        self._probes: dict[str, _Probe] = {}

    async def inspect(self, host: str, user: str, revision: str) -> dict[str, Any]:
        host = validate_network_host(host)
        snapshot = await inspect_gateway(host)
        now = time.monotonic()
        self._probes = {
            key: probe for key, probe in self._probes.items()
            if probe.expires > now and probe.user != user
        }
        while len(self._probes) >= _MAX_PROBES:
            self._probes.pop(next(iter(self._probes)))
        token = secrets.token_urlsafe(32)
        self._probes[token] = _Probe(
            user, host, revision, now + _PROBE_LIFETIME_SECONDS, snapshot,
        )
        return {
            "adapter_token": token,
            "model": snapshot.model,
            "firmware": snapshot.firmware,
            "identifier": snapshot.identifier,
            "tcp_port": snapshot.tcp_port,
            "baudrate": snapshot.baudrate,
            "frame": snapshot.frame,
            "connection_type": snapshot.connection_type,
            "tcp_authentication": snapshot.tcp_authentication,
            "can_configure": snapshot.can_configure,
        }

    def expected(self, token: object, user: str, host: str, revision: str) -> GatewaySnapshot:
        if not isinstance(token, str) or len(token) > 64:
            raise ValueError("adapter detection is required")
        probe = self._probes.get(token)
        if (
            probe is None or probe.expires <= time.monotonic()
            or probe.user != user or probe.host != validate_network_host(host)
            or probe.revision != revision
        ):
            raise ValueError("adapter detection has expired or settings changed")
        return probe.snapshot

    def forget(self, token: str) -> None:
        self._probes.pop(token, None)
