"""Search a serial bus for an AMTRON wallbox.

The wallbox's Modbus address and bus parameters are configurable with the
MENNEKES configuration tool, so a user who inherited an installation often
does not know them. Rather than making them guess, the config flow can try
the documented combinations and report what answered.

The search is read-only: it reads the Modbus layout register and nothing
else, so it cannot put a wallbox into the energy-manager error state.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .client import MennekesModbusClient, SerialConfig
from .client_errors import AmtronBusError, AmtronConnectionError
from .const import (
    DEVICE_ID_MAX,
    DEVICE_ID_MIN,
    SUPPORTED_BAUDRATES,
    SUPPORTED_PARITY_STOPBITS,
)
from .data import DeviceIdentity
from .identity import async_read_identity
from .registers import MODBUS_LAYOUT_VERSION

_LOGGER = logging.getLogger(__name__)

# A probe only has to see whether something answers, so it waits briefly and
# does not retry. A full search is hundreds of probes.
PROBE_TIMEOUT = 0.5
PROBE_RETRIES = 1
DEVICE_IDS = range(DEVICE_ID_MIN, DEVICE_ID_MAX + 1)

# How many candidates in a row may fail to open the port before the search
# gives up on it. One failure is not enough to conclude anything: the adapter
# can be busy for a moment, and a baud rate the driver cannot set fails here
# too, while the next candidate would have opened fine. A run of failures is
# what a genuinely unusable port looks like.
CONNECT_FAILURE_LIMIT = 5


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What a search found."""

    config: SerialConfig
    identity: DeviceIdentity
    attempts: int


def search_space(
    base: SerialConfig,
    *,
    all_parameters: bool,
) -> list[SerialConfig]:
    """Return the bus configurations a search will try, in order.

    The user's own values come first, then the factory defaults, so the common
    cases are found in the first few probes.
    """

    frames = sorted(
        SUPPORTED_PARITY_STOPBITS
        if all_parameters
        else ((base.parity, base.stopbits),),
        key=lambda frame: frame != (base.parity, base.stopbits),
    )
    baudrates = sorted(
        SUPPORTED_BAUDRATES if all_parameters else (base.baudrate,),
        key=lambda value: value != base.baudrate,
    )
    device_ids = sorted(DEVICE_IDS, key=lambda value: value != base.device_id)

    space: list[SerialConfig] = []
    for baudrate in baudrates:
        for parity, stopbits in frames:
            for device_id in device_ids:
                space.append(  # noqa: PERF401 - the nesting is the search order
                    SerialConfig(
                        port=base.port,
                        baudrate=baudrate,
                        bytesize=base.bytesize,
                        parity=parity,
                        stopbits=stopbits,
                        device_id=device_id,
                        timeout=PROBE_TIMEOUT,
                        retries=PROBE_RETRIES,
                    )
                )
    return space


async def async_search(
    base: SerialConfig,
    *,
    all_parameters: bool,
    probe: Callable[[SerialConfig], Awaitable[DeviceIdentity | None]] | None = None,
) -> SearchResult | None:
    """Try every candidate configuration and return the first that answers."""

    attempt = probe or async_probe
    space = search_space(base, all_parameters=all_parameters)
    _LOGGER.debug("Searching %s with %d candidates", base.port, len(space))
    failures = 0
    for index, candidate in enumerate(space, start=1):
        try:
            identity = await attempt(candidate)
        except AmtronConnectionError as err:
            failures += 1
            _LOGGER.debug(
                "Candidate %d of %d could not open %s: %s",
                index,
                len(space),
                candidate.port,
                err,
            )
            if failures >= CONNECT_FAILURE_LIMIT:
                raise
            continue
        failures = 0
        if identity is not None:
            _LOGGER.debug(
                "Found a wallbox at address %d, %d baud after %d probes",
                candidate.device_id,
                candidate.baudrate,
                index,
            )
            return SearchResult(config=candidate, identity=identity, attempts=index)
    return None


async def async_probe(config: SerialConfig) -> DeviceIdentity | None:
    """Return the identity of a wallbox at this configuration, if any."""

    client = MennekesModbusClient(config)
    try:
        await client.async_connect()
    except AmtronConnectionError:
        # Raised on to the search, which decides whether this was one bad
        # candidate or a port that cannot be used at all.
        raise
    try:
        layout = await client.async_read_register(MODBUS_LAYOUT_VERSION)
        if not isinstance(layout, int) or layout <= 0:
            return None
        return await async_read_identity(client)
    except AmtronBusError:
        return None
    finally:
        await client.async_close()
