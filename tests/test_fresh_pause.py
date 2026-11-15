"""An automatic P&C resume needs current evidence, including across the service API."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession

from custom_components.mennekes_amtron_rtu.control import ControlOptions
from custom_components.mennekes_amtron_rtu.device.data import WriteDiagnostics
from custom_components.mennekes_amtron_rtu.remote import RemoteClient, RemoteControl
from tests.test_control import Harness
from tests.test_modbus_service import TOKEN, service_fixture
from tests.test_plug_charge_observer import observer_harness, plug_in_paused


@pytest.mark.parametrize(("value", "expected"), [
    (0, False), (1, True), (5.99, True), (6, False), (16, False),
    (None, None), (True, None), (-1, None), (float("nan"), None), (float("inf"), None),
])
def test_fresh_pause_uses_valid_current_without_mutating_intent(value, expected):
    h = Harness()
    h.snapshot.values["charging_current_ems"] = value
    h.control.pause_requested = True
    assert h.control.observed_pause is expected
    assert h.control.pause_requested is True


@pytest.mark.parametrize("failure", ["held", "failed_block", "generation", "missing"])
def test_cached_pause_is_not_fresh_evidence(failure):
    h = Harness(values={"charging_current_ems": 1.0})
    assert h.control.is_paused is True
    if failure == "held":
        h.snapshot.held = frozenset({"charging_current_ems"})
    elif failure == "failed_block":
        h.snapshot.failed_blocks = ("current_limits",)
    elif failure == "generation":
        h.snapshot.current_write_generation += 1
    else:
        h.snapshot.values.clear()
    assert h.control.is_paused is True
    assert h.control.observed_pause is None


def test_remote_failed_block_keeps_a_cached_pause_out_of_automatic_decisions(tmp_path):
    async def run():
        service, _transport, _clock = await service_fixture(tmp_path, current=1)
        try:
            async with ClientSession() as session:
                client = RemoteClient(session, "http://service:8765", TOKEN)
                data = client.apply_snapshot(service.snapshot())
                control = RemoteControl(client, diagnostics=WriteDiagnostics(),
                                        options=ControlOptions(False, 16), state=lambda: data,
                                        request_refresh=None)
                assert control.is_paused is True
                assert control.observed_pause is True
                service.data.failed_blocks = ("current_limits",)
                service.data.held = frozenset({"charging_current_ems"})
                data = client.apply_snapshot(service.snapshot())
                assert control.is_paused is True
                assert control.observed_pause is None
        finally:
            await service.stop()
    asyncio.run(run())


def test_pc_observer_waits_for_new_readback_instead_of_resuming_a_cached_pause(tmp_path, monkeypatch):
    async def run():
        async with observer_harness(tmp_path, monkeypatch) as h:
            await plug_in_paused(h)
            h.control.is_paused = True
            h.control.observed_pause = None
            h.timers[-1].callback(None)
            await h.hass.async_block_till_done()
            h.control.async_resume_charging.assert_not_awaited()
            h.control.async_authorize_plug_charge.assert_not_awaited()
            assert "register_unavailable" in h.observer._release_blockers()
    asyncio.run(run())
