"""Resuming opens the release again that the wallbox closed with the pause.

With a car plugged in, the AMTRON closes 0x0D05 itself when the current
drops to the pause value and never opens it again on its own.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device import registers as R
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
)
from custom_components.mennekes_amtron_rtu.device.data import WallboxData
from custom_components.mennekes_amtron_rtu.device.enums import CpState, EvseState
from tests.fakes import put
from tests.test_control import Harness


def _paused(*, release: int, state: EvseState = EvseState.PRECONDITIONS_VALID) -> Harness:
    harness = Harness(values={"charging_current_ems": 12.0}, clock=[1000.0])
    put(harness.transport.bank, R.EVSE_STATE, state)
    put(harness.transport.bank, R.CP_STATE, CpState.B1 if state != EvseState.IDLE else CpState.A1)
    put(harness.transport.bank, R.CHARGING_RELEASE, release)
    return harness


def _resume(harness: Harness, *, withdrawn: bool = False) -> None:
    async def run() -> None:
        control = await harness.connect()
        control.charging_release_requested = not withdrawn
        await control.async_pause_charging()
        harness.clock[0] += 400
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.transport.writes.clear()
        await control.async_resume_charging()

    asyncio.run(run())


def test_resuming_with_a_car_opens_the_release_before_the_current():
    harness = _paused(release=0)
    _resume(harness)
    addresses = [address for address, _ in harness.transport.writes]
    assert harness.written(R.CHARGING_RELEASE) == [[1]]
    assert addresses.index(R.CHARGING_RELEASE.address) < addresses.index(R.CHARGING_CURRENT_EMS.address)


@pytest.mark.parametrize(("release", "state", "withdrawn"), [
    # Open already, as without a car: nothing to do.
    (1, EvseState.PRECONDITIONS_VALID, False),
    (1, EvseState.IDLE, False),
    # Switched off by hand: resuming does not overrule it.
    (0, EvseState.PRECONDITIONS_VALID, True),
])
def test_resuming_leaves_the_release_alone_otherwise(release, state, withdrawn):
    harness = _paused(release=release, state=state)
    _resume(harness, withdrawn=withdrawn)
    assert harness.written(R.CHARGING_RELEASE) == []
    assert harness.written(R.CHARGING_CURRENT_EMS)


def test_a_current_set_from_the_pause_opens_the_release_as_well():
    # The departure plan starts its charge after its own pause this way.
    harness = _paused(release=0)

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.clock[0] += 400
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.transport.writes.clear()
        await control.async_set_charging_current(10)

    asyncio.run(run())
    assert [address for address, _ in harness.transport.writes] == [
        R.CHARGING_RELEASE.address, R.CHARGING_CURRENT_EMS.address,
    ]


def test_a_current_set_while_charging_leaves_the_release_alone():
    harness = _paused(release=0, state=EvseState.CHARGING)

    async def run() -> None:
        control = await harness.connect()
        harness.snapshot = WallboxData(values={"charging_current_ems": 12.0})
        await control.async_set_charging_current(10)

    asyncio.run(run())
    assert harness.written(R.CHARGING_RELEASE) == []


def test_a_resume_that_must_wait_opens_nothing_before_it_runs():
    harness = Harness(values={"charging_current_ems": 12.0}, clock=[1000.0], state_change_interval_seconds=300)
    put(harness.transport.bank, R.EVSE_STATE, EvseState.PRECONDITIONS_VALID)
    put(harness.transport.bank, R.CP_STATE, CpState.B1)

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        put(harness.transport.bank, R.CHARGING_CURRENT_EMS, 1.0)
        put(harness.transport.bank, R.CHARGING_RELEASE, 0)
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        harness.transport.writes.clear()
        await control.async_resume_charging()
        # Queued as a whole: the release stays closed meanwhile.
        assert control.pending_action == "resume"
        assert harness.transport.writes == []
        harness.clock[0] += 400
        await control.async_resume_charging()
        assert [address for address, _ in harness.transport.writes] == [
            R.CHARGING_RELEASE.address, R.CHARGING_CURRENT_EMS.address,
        ]
        await control.async_shutdown()

    asyncio.run(run())


def test_without_a_car_a_closed_release_stays_closed():
    harness = _paused(release=0, state=EvseState.IDLE)
    _resume(harness)
    assert harness.written(R.CHARGING_RELEASE) == []


@pytest.mark.parametrize("held", [16.0, "read_error"])
def test_a_release_is_not_opened_ahead_unless_the_pause_current_holds(held):
    """Above the pause current the release itself would start the charge."""
    harness = _paused(release=0)

    async def run() -> None:
        control = await harness.connect()
        await control.async_pause_charging()
        harness.clock[0] += 400
        harness.snapshot = WallboxData(values={"charging_current_ems": 1.0})
        if held == "read_error":
            original = harness.client.async_read_register

            async def failing(spec, *args, **kwargs):
                if spec == R.CHARGING_CURRENT_EMS:
                    raise AmtronConnectionError("port closed")
                return await original(spec, *args, **kwargs)

            harness.client.async_read_register = failing
        else:
            put(harness.transport.bank, R.CHARGING_CURRENT_EMS, held)
        harness.transport.writes.clear()
        await control.async_resume_charging()

    asyncio.run(run())
    assert harness.written(R.CHARGING_RELEASE) == []
    assert harness.written(R.CHARGING_CURRENT_EMS)
