"""After an unanswered request the line falls silent before the next one."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.mennekes_amtron_rtu.device.bus_quiet import BusQuiet
from custom_components.mennekes_amtron_rtu.device.client_errors import (
    AmtronConnectionError,
    AmtronIllegalAddressError,
    AmtronProtocolError,
)


def test_an_answered_request_and_a_proper_refusal_cost_no_wait():
    async def run():
        quiet = BusQuiet(quiet=0.05)
        loop = asyncio.get_running_loop()

        async def answered():
            quiet.packet(True, b"q")
            assert quiet.packet(False, b"a") == b"a"
            return 7

        async def refused():
            raise AmtronIllegalAddressError("unknown", exception_code=2)

        started = loop.time()
        assert await quiet.ask(answered) == 7
        with pytest.raises(AmtronIllegalAddressError):
            await quiet.ask(refused)
        assert await quiet.ask(answered) == 7
        assert loop.time() - started < 0.04

    asyncio.run(run())


@pytest.mark.parametrize("failure", [
    AmtronProtocolError("no answer"), AmtronConnectionError("lost"), asyncio.CancelledError(),
])
def test_after_a_request_without_an_answer_the_next_waits_for_silence(failure):
    async def run():
        quiet = BusQuiet(quiet=0.05)
        loop = asyncio.get_running_loop()

        async def unanswered():
            raise failure

        async def answered():
            return loop.time()

        with pytest.raises(type(failure)):
            await quiet.ask(unanswered)
        failed_at = loop.time()
        # The late answer arrives meanwhile: the silence counts from there.
        loop.call_later(0.03, quiet.packet, False, b"late")
        assert await quiet.ask(answered) - failed_at >= 0.075
        # Settled: the next request goes out at once.
        again = loop.time()
        assert await quiet.ask(answered) - again < 0.04

    asyncio.run(run())


def test_bytes_nobody_asked_for_and_a_retry_make_the_next_request_wait():
    async def run():
        quiet = BusQuiet(quiet=0.05)
        loop = asyncio.get_running_loop()

        async def now():
            return loop.time()

        for mark in (lambda: quiet.packet(False, b"stray"), quiet.unanswered):
            mark()
            marked = loop.time()
            assert await quiet.ask(now) - marked >= 0.045
        # A link whose frames carry a transaction number never waits.
        numbered = BusQuiet(quiet=5, enabled=False)
        numbered.unanswered()
        started = loop.time()
        await numbered.settle()
        assert loop.time() - started < 0.04

    asyncio.run(run())
