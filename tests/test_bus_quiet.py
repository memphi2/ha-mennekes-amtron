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


def test_a_retry_makes_the_next_request_wait_and_a_stray_byte_alone_does_not():
    async def run():
        quiet = BusQuiet(quiet=0.05)
        loop = asyncio.get_running_loop()

        async def now():
            return loop.time()

        # A byte trailing a good answer is no answer out of step.
        quiet.packet(False, b"stray")
        marked = loop.time()
        assert await quiet.ask(now) - marked < 0.04
        quiet.unanswered()
        marked = loop.time()
        assert await quiet.ask(now) - marked >= 0.045
        # A link whose frames carry a transaction number never waits.
        numbered = BusQuiet(quiet=0.5, enabled=False)
        numbered.unanswered()
        started = loop.time()
        await numbered.settle()
        assert loop.time() - started < 0.04

    asyncio.run(run())


def test_a_line_that_is_never_silent_still_carries_the_next_request():
    """Noise extends the wait only up to two quiet periods: the heartbeat goes out."""
    async def run():
        quiet = BusQuiet(quiet=0.05)
        loop = asyncio.get_running_loop()

        async def noise():
            while True:
                quiet.packet(False, b"x")
                await asyncio.sleep(0.01)

        noisy = asyncio.create_task(noise())
        try:
            quiet.unanswered()
            started = loop.time()
            await asyncio.wait_for(quiet.settle(), 1)
            assert 0.09 <= loop.time() - started < 0.2
        finally:
            noisy.cancel()

    asyncio.run(run())


def test_a_long_reply_timeout_does_not_lengthen_the_wait_beyond_a_second():
    assert BusQuiet(quiet=5)._quiet == 1.0
    assert BusQuiet(quiet=0.3)._quiet == 0.3
