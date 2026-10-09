"""Tests for the bus search the config flow offers."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron._flow_search import (
    CONNECT_FAILURE_LIMIT,
    DEVICE_IDS,
    PROBE_RETRIES,
    PROBE_TIMEOUT,
    async_probe,
    async_search,
    search_space,
)
from custom_components.mennekes_amtron.client import SerialConfig
from custom_components.mennekes_amtron.client_errors import AmtronConnectionError
from custom_components.mennekes_amtron.const import (
    SUPPORTED_BAUDRATES,
    SUPPORTED_PARITY_STOPBITS,
)
from custom_components.mennekes_amtron.data import DeviceIdentity
from tests.fakes import FakeModbusClient, device_bank

BASE = SerialConfig(
    port="/dev/fake", baudrate=57600, parity="N", stopbits=2, device_id=50
)


def test_the_address_search_covers_every_documented_address() -> None:
    space = search_space(BASE, all_parameters=False)
    assert len(space) == len(DEVICE_IDS)
    assert {candidate.device_id for candidate in space} == set(DEVICE_IDS)
    assert {candidate.baudrate for candidate in space} == {57600}


def test_the_users_own_values_are_tried_first() -> None:
    base = SerialConfig(
        port="/dev/fake", baudrate=19200, parity="E", stopbits=1, device_id=23
    )
    first = search_space(base, all_parameters=True)[0]
    assert (first.device_id, first.baudrate, first.parity, first.stopbits) == (
        23,
        19200,
        "E",
        1,
    )


def test_the_full_search_covers_every_combination_once() -> None:
    space = search_space(BASE, all_parameters=True)
    keys = {
        (c.baudrate, c.parity, c.stopbits, c.device_id) for c in space
    }
    assert len(keys) == len(space)
    assert len(space) == (
        len(SUPPORTED_BAUDRATES)
        * len(SUPPORTED_PARITY_STOPBITS)
        * len(DEVICE_IDS)
    )


def test_a_probe_waits_briefly_and_does_not_retry() -> None:
    candidate = search_space(BASE, all_parameters=False)[0]
    assert candidate.timeout == PROBE_TIMEOUT
    assert candidate.retries == PROBE_RETRIES


def test_the_search_stops_at_the_first_answer() -> None:
    tried: list[int] = []

    async def probe(config: SerialConfig) -> DeviceIdentity | None:
        tried.append(config.device_id)
        if config.device_id == 23:
            return DeviceIdentity(layout_version=0x0103, serial_number="S")
        return None

    result = asyncio.run(async_search(BASE, all_parameters=False, probe=probe))

    assert result is not None
    assert result.config.device_id == 23
    assert result.identity.serial_number == "S"
    assert result.attempts == len(tried)
    assert tried[-1] == 23


def test_an_empty_bus_returns_nothing() -> None:
    async def probe(_config: SerialConfig) -> DeviceIdentity | None:
        return None

    assert asyncio.run(async_search(BASE, all_parameters=False, probe=probe)) is None


def test_a_probe_reads_the_identity_of_a_wallbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeModbusClient(device_bank())
    monkeypatch.setattr(
        "custom_components.mennekes_amtron._flow_search.MennekesModbusClient",
        lambda config: _client(config, transport),
    )
    identity = asyncio.run(async_probe(BASE))
    assert identity is not None
    assert identity.serial_number == "ABC123456789"
    assert transport.close_calls == 1


def test_a_probe_on_a_silent_address_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeModbusClient({})
    monkeypatch.setattr(
        "custom_components.mennekes_amtron._flow_search.MennekesModbusClient",
        lambda config: _client(config, transport),
    )
    assert asyncio.run(async_probe(BASE)) is None


def test_a_probe_on_a_rejecting_address_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeModbusClient(device_bank())
    transport.read_exceptions[0x0000] = 0x02
    monkeypatch.setattr(
        "custom_components.mennekes_amtron._flow_search.MennekesModbusClient",
        lambda config: _client(config, transport),
    )
    assert asyncio.run(async_probe(BASE)) is None


def test_a_port_that_will_not_open_makes_the_probe_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe reports it; the search decides what it means."""

    transport = FakeModbusClient({})
    transport.connect_result = False
    monkeypatch.setattr(
        "custom_components.mennekes_amtron._flow_search.MennekesModbusClient",
        lambda config: _client(config, transport),
    )
    with pytest.raises(AmtronConnectionError):
        asyncio.run(async_probe(BASE))


def _client(config: SerialConfig, transport: FakeModbusClient) -> object:
    from custom_components.mennekes_amtron.client import MennekesModbusClient

    return MennekesModbusClient(config, client_factory=lambda _c: transport)


# --- a failed port open is not the end of the search -----------------------


def test_one_candidate_that_cannot_open_the_port_is_skipped() -> None:
    """A single failure proves nothing about the remaining candidates.

    The adapter can be busy for a moment, and a baud rate the driver cannot
    set fails at the open too. Giving up there reported "not found" for a
    wallbox that sits later in the search space.
    """

    attempts: list[SerialConfig] = []
    target = search_space(BASE, all_parameters=True)[20]

    async def probe(config: SerialConfig) -> DeviceIdentity | None:
        attempts.append(config)
        if len(attempts) == 1:
            raise AmtronConnectionError("device or resource busy")
        if config.device_id == target.device_id:
            return DeviceIdentity(layout_version=0x0103)
        return None

    result = asyncio.run(async_search(BASE, all_parameters=True, probe=probe))
    assert result is not None
    assert result.config.device_id == target.device_id
    assert len(attempts) == 21


def test_a_run_of_failed_opens_gives_up_on_the_port() -> None:
    """A port that is really unusable must not cost the full search."""

    attempts: list[SerialConfig] = []

    async def probe(config: SerialConfig) -> DeviceIdentity | None:
        attempts.append(config)
        raise AmtronConnectionError("no such device")

    with pytest.raises(AmtronConnectionError):
        asyncio.run(async_search(BASE, all_parameters=True, probe=probe))
    assert len(attempts) == CONNECT_FAILURE_LIMIT


def test_scattered_failures_do_not_add_up_to_an_unusable_port() -> None:
    """The limit counts failures in a row, not failures in total."""

    attempts: list[SerialConfig] = []

    async def probe(config: SerialConfig) -> DeviceIdentity | None:
        attempts.append(config)
        if len(attempts) % 2 == 1:
            raise AmtronConnectionError("device or resource busy")
        return None

    assert asyncio.run(async_search(BASE, all_parameters=False, probe=probe)) is None
    assert len(attempts) == len(DEVICE_IDS)
